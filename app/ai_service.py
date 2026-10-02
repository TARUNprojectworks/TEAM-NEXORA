"""Every Gemini call lives here and nowhere else.

Public functions (all return plain Python data):
    make_cards(text=None, files=None)          -> [card dict, ...]
    generate_more_cards(deck_title, topics, existing_cards, count) -> [card dict, ...]
    explain_card(card)                          -> str, 3-4 plain sentences
    fix_weak_spot(topic, wrong_cards)           -> {"explanation": str, "follow_up_cards": [3 card dicts]}
    find_resources(topic)                       -> {"links": [{"title", "uri"}], "search_widget_html": str | None}
A card dict is {"question", "answer", "topic", "importance", "source_line"}.

With AI_ENABLED=false every function returns realistic stand-in data with
exactly the same shape, so the whole app works on a laptop without Google Cloud.
The stand-ins build cards from the student's own notes or cards, so they are
never wrong; they are just less clever than Gemini.

Rules from CLAUDE.md: structured JSON output for cards, the Google Search tool
only for find_resources (never mixed with JSON output), 15 s timeout,
temperature 0.3, 20 calls per student per day, and we log only the feature,
latency and ok/error, never the notes or card text. We send only study text
and note files, never names, emails or passwords.
"""

import json
import logging
import re
import time
from functools import lru_cache
from urllib.parse import quote_plus

from flask import current_app

log = logging.getLogger("nexora.ai")

COULD_NOT_READ = "Couldn't read that. Try a shorter piece of notes."
TEMPERATURE = 0.3

# How much Gemini "thinks" before answering. Measured on gemini-3.5-flash (2 Oct):
# writing cards: default thinking took 13-17 s for a page of notes (over our 15 s limit),
# LOW took 6-9 s with the same cards. Explain and link search: LOW about 3.5 s and 6.4 s,
# MINIMAL about 1.4 s and 2.4 s. Note: gemini-3.8-flash doesn't accept MINIMAL.
THINKING_FOR_CARDS = "LOW"
THINKING_FOR_SHORT_REPLIES = "MINIMAL"
MAX_DRAFTS = 25
MAX_EXISTING_QUESTIONS = 100
IMPORTANCE_LEVELS = ("high", "medium", "low")


class AIError(Exception):
    """Gemini failed or sent something we can't use. The message is shown to the student."""


class AILimitReached(AIError):
    pass


# ---------- Daily limit ----------

def calls_left(user, today):
    used = user.ai_calls_today if user.ai_calls_date == today else 0
    return max(current_app.config["AI_DAILY_LIMIT"] - used, 0)


def use_ai_call(user, today):
    """Count one AI call for this student, or stop if today's limit is used up.

    The caller commits. A failed call still counts: it still cost a request.
    """
    if user.ai_calls_date != today:
        user.ai_calls_date = today
        user.ai_calls_today = 0
    if user.ai_calls_today >= current_app.config["AI_DAILY_LIMIT"]:
        limit = current_app.config["AI_DAILY_LIMIT"]
        raise AILimitReached(f"You've used today's {limit} AI calls. Try again tomorrow, or use Quick split.")
    user.ai_calls_today += 1


# ---------- Logging ----------

def logged_call(feature, work):
    """Run one AI feature and log how it went. Never logs what was sent or received."""
    started = time.perf_counter()
    try:
        result = work()
    except Exception as error:
        log.warning("ai_call", extra={"fields": {
            "feature": feature, "ok": False, "error": type(error).__name__,
            "status": getattr(error, "code", None),  # the HTTP status from Google, e.g. 429 or 504
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "ai_enabled": ai_enabled(),
        }})
        raise
    log.info("ai_call", extra={"fields": {
        "feature": feature, "ok": True,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "ai_enabled": ai_enabled(),
    }})
    return result


def ai_enabled():
    return bool(current_app.config["AI_ENABLED"])


# ---------- Checking what comes back ----------

def clean_card(raw, default_topic=""):
    """One card dict with every field present and sensible, or None if unusable."""
    if not isinstance(raw, dict):
        return None
    question = str(raw.get("question") or "").strip()
    answer = str(raw.get("answer") or "").strip()
    if not question or not answer:
        return None
    importance = str(raw.get("importance") or "medium").strip().lower()
    return {
        "question": question[:1000],
        "answer": answer[:2000],
        "topic": (str(raw.get("topic") or "").strip() or default_topic)[:80],
        "importance": importance if importance in IMPORTANCE_LEVELS else "medium",
        "source_line": str(raw.get("source_line") or "").strip()[:300],
    }


def clean_cards(raw_cards, default_topic=""):
    if not isinstance(raw_cards, list):
        raise AIError(COULD_NOT_READ)
    cards = [card for card in (clean_card(raw, default_topic) for raw in raw_cards) if card]
    if not cards:
        raise AIError(COULD_NOT_READ)
    return cards[:MAX_DRAFTS]


# ---------- Public functions ----------

def make_cards(text=None, files=None, topics=None):
    """Draft cards from notes text and/or files.

    files: [{"mime_type", "data": bytes}] on a laptop, or [{"mime_type", "uri": "gs://..."}] on GCP.
    topics: the target deck's existing topics, so new cards reuse them.
    """
    files = files or []
    if not ai_enabled():
        return logged_call("make_cards", lambda: fake_make_cards(text, files))

    def ask():
        cards = clean_cards(gemini_make_cards(text, files, topics or []))
        return keep_real_source_lines(cards, text) if text and not files else cards

    return logged_call("make_cards", ask)


def generate_more_cards(deck_title, topics, existing_cards, count):
    """New cards for a deck. Only the questions (max 100) are sent, to avoid repeats."""
    if not ai_enabled():
        return logged_call("generate_more", lambda: fake_generate_more(topics, existing_cards, count))
    questions = [card["question"] for card in existing_cards][:MAX_EXISTING_QUESTIONS]
    return logged_call("generate_more", lambda: clean_cards(
        gemini_generate_more(deck_title, topics, questions, count))[:count])


def explain_card(card):
    """A short, plain explanation of one card. The caller caches it in cards.explanation."""
    if not ai_enabled():
        return logged_call("explain", lambda: fake_explain(card.question, card.answer))
    return logged_call("explain", lambda: gemini_explain(card.question, card.answer))


def fix_weak_spot(topic, wrong_cards):
    """An explanation of the weak topic and 3 practice cards.

    wrong_cards: [{"question", "answer"}] the student got wrong in this topic.
    """
    if not ai_enabled():
        return logged_call("fix_weak_spot", lambda: fake_fix_weak_spot(topic, wrong_cards))

    def ask():
        raw = gemini_fix_weak_spot(topic, wrong_cards)
        explanation = str(raw.get("explanation") or "").strip() if isinstance(raw, dict) else ""
        cards = clean_cards(raw.get("follow_up_cards") if isinstance(raw, dict) else None, topic)
        if not explanation or len(cards) < 3:
            raise AIError(COULD_NOT_READ)
        return {"explanation": explanation[:1500], "follow_up_cards": cards[:3]}

    return logged_call("fix_weak_spot", ask)


def find_resources(topic):
    """Real links for the topic, from Grounding with Google Search, with search fallbacks."""
    if not ai_enabled():
        return {"links": fallback_links(topic), "search_widget_html": None}
    try:
        return logged_call("find_resources", lambda: gemini_find_resources(topic))
    except Exception:
        return {"links": fallback_links(topic), "search_widget_html": None}


def fallback_links(topic):
    """Plain search links built from the topic. Used when AI is off or grounding fails."""
    query = quote_plus(topic)
    return [
        {"title": f"Videos about {topic} on YouTube", "uri": f"https://www.youtube.com/results?search_query={query}"},
        {"title": f"Search {topic} on Google", "uri": f"https://www.google.com/search?q={query}"},
        {"title": f"{topic} on Wikipedia", "uri": f"https://en.wikipedia.org/w/index.php?search={query}"},
    ]


# ---------- Gemini (real calls; only used when AI_ENABLED=true) ----------

CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "answer": {"type": "string"},
        "topic": {"type": "string"},
        "importance": {"type": "string", "enum": list(IMPORTANCE_LEVELS)},
        "source_line": {"type": "string"},
    },
    "required": ["question", "answer", "topic", "importance", "source_line"],
}
CARDS_SCHEMA = {"type": "array", "items": CARD_SCHEMA, "maxItems": MAX_DRAFTS}
FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "follow_up_cards": {"type": "array", "items": CARD_SCHEMA, "minItems": 3, "maxItems": 3},
    },
    "required": ["explanation", "follow_up_cards"],
}

CARD_RULES = """Rules for every card:
- One idea per card. The question asks about exactly one thing and is short and clear.
- Ask open questions (what, why, how, which). No yes/no questions.
- Each card makes sense on its own, months later, without the notes. Never refer to the
  source: no "in the notes", "in this study", "in this investigation", "in this scenario",
  "according to the text". Name the subject instead.
  Bad: "What is tshark used for in this investigation?"
  Good: "What is tshark used for in network forensics?"
- The answer is correct, in simple English, and short: ideally under 20 words.
- topic is a short chapter-style name (1 to 4 words). Use 2 to 6 topics for the whole set and
  reuse them across cards. Prefer the notes' own headings.
- importance: high for core ideas a student must know for an exam, medium for normal facts,
  low for side details."""


def gemini_client():
    config = current_app.config
    return shared_client(config["GCP_PROJECT"], config["GCP_LOCATION"], config["AI_TIMEOUT_SECONDS"])


@lru_cache(maxsize=4)
def shared_client(project, location, timeout_seconds):
    """One Gemini client per worker process, reused for every call.

    Reusing it saves a Google login on each request. It also keeps the client
    alive during the call: a client that is thrown away closes its connection.
    """
    # Imported here so the app runs on a laptop without Google Cloud libraries set up.
    # Auth is the VM's service account (Application Default Credentials): no API keys.
    from google import genai
    from google.genai import types

    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options=types.HttpOptions(
            timeout=timeout_seconds * 1000,  # per attempt
            # Gemini's response time has a long tail: most calls take ~5 s, but now and then one
            # hangs past the deadline (we saw 21 s and 59 s). One quick retry on a timeout or
            # "busy" reply almost always lands. Worst case for the student: two attempts.
            retry_options=types.HttpRetryOptions(
                attempts=2, initial_delay=0.5, max_delay=1.0, http_status_codes=[408, 429, 500, 502, 503, 504],
            ),
        ),
    )


def generation_config(json_schema=None, search=False, thinking=THINKING_FOR_CARDS):
    from google.genai import types

    safety = [
        types.SafetySetting(category=category, threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE)
        for category in (
            types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        )
    ]
    # We never let Gemini call our Python functions, so switch that SDK feature off.
    no_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
    thinking_config = types.ThinkingConfig(thinking_level=types.ThinkingLevel[thinking])
    if search:
        # Grounding with Google Search: no JSON schema in the same call.
        return types.GenerateContentConfig(
            temperature=TEMPERATURE, safety_settings=safety, automatic_function_calling=no_function_calling,
            thinking_config=thinking_config, tools=[types.Tool(google_search=types.GoogleSearch())],
        )
    return types.GenerateContentConfig(
        temperature=TEMPERATURE, safety_settings=safety, automatic_function_calling=no_function_calling,
        thinking_config=thinking_config,
        response_mime_type="application/json" if json_schema else None,
        response_json_schema=json_schema,
    )


def ask_gemini(contents, json_schema=None, search=False, thinking=THINKING_FOR_CARDS):
    response = gemini_client().models.generate_content(
        model=current_app.config["GEMINI_MODEL"],
        contents=contents,
        config=generation_config(json_schema, search, thinking),
    )
    return response


def ask_gemini_json(contents, json_schema):
    response = ask_gemini(contents, json_schema)
    try:
        return json.loads(response.text or "")
    except (TypeError, ValueError):
        raise AIError(COULD_NOT_READ)


def file_parts(files):
    from google.genai import types

    parts = []
    for file in files:
        if "uri" in file:
            parts.append(types.Part.from_uri(file_uri=file["uri"], mime_type=file["mime_type"]))
        else:
            parts.append(types.Part.from_bytes(data=file["data"], mime_type=file["mime_type"]))
    return parts


def gemini_make_cards(text, files, topics):
    prompt = f"""You make flashcards for a student from their own study notes (typed text, photos of
handwritten pages, or PDFs).
Make about one card per key idea, up to {MAX_DRAFTS} cards. Don't pad with trivial cards.
{CARD_RULES}
- source_line: copy the words from the notes that the card comes from, word for word, as written
  (keep the student's spelling). Keep it short: one line or phrase, under 160 characters.
- Use only what the notes say. Don't add facts that aren't there.
- Handwriting: if a word or line is unclear, skip it rather than guess. Fix obvious spelling
  mistakes in the question and answer (not in source_line). Expand a short form only when the
  notes give its full name; otherwise keep it as written.
- Skip personal details such as names, roll numbers, dates and email addresses: no cards about them."""
    if topics:
        prompt += "\nThe deck already uses these topics; reuse them where they fit: " + ", ".join(topics)
    contents = [prompt] + file_parts(files)
    if text:
        contents.append("Notes:\n" + text)
    return ask_gemini_json(contents, CARDS_SCHEMA)


def squash(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def keep_real_source_lines(cards, text):
    """For typed notes we can check the quote: blank any source_line that isn't really in the notes."""
    notes = squash(text)
    for card in cards:
        if card["source_line"] and squash(card["source_line"]) not in notes:
            card["source_line"] = ""
    return cards


def gemini_generate_more(deck_title, topics, questions, count):
    prompt = (
        f"A student is studying a flashcard deck called {deck_title!r} with these topics: "
        f"{', '.join(topics)}. Write {count} new flashcards on the same topics that are not "
        f"already in the deck. {CARD_RULES} Set source_line to an empty string.\n"
        "Questions already in the deck:\n" + "\n".join(f"- {q}" for q in questions)
    )
    return ask_gemini_json([prompt], CARDS_SCHEMA)


def gemini_explain(question, answer):
    prompt = (
        "Explain this flashcard to a student in 3 or 4 short, plain sentences. Say why the answer "
        "is right and give one simple way to remember it. Plain text only: no Markdown, no "
        "asterisks, no headings, no lists.\n"
        f"Question: {question}\nAnswer: {answer}"
    )
    response = ask_gemini([prompt], thinking=THINKING_FOR_SHORT_REPLIES)
    # We show it as plain text, so drop any Markdown bold that slips through.
    text = (response.text or "").replace("**", "").strip()
    if not text:
        raise AIError("Couldn't explain this card right now. Try again in a minute.")
    return text[:1500]


def gemini_fix_weak_spot(topic, wrong_cards):
    missed = "\n".join(f"- Q: {c['question']} A: {c['answer']}" for c in wrong_cards)
    prompt = (
        f"A student keeps getting the topic {topic!r} wrong. These are cards they missed:\n{missed}\n"
        "Write an explanation of the topic in 4 to 6 short, plain sentences that clears up the "
        "likely confusion, then exactly 3 new practice flashcards that test the same ideas in a "
        f"different way. {CARD_RULES} Use importance high and set source_line to an empty string."
    )
    return ask_gemini_json([prompt], FIX_SCHEMA)


def gemini_find_resources(topic):
    prompt = (
        f"Find 3 to 5 good, free resources (articles or videos) for a student learning {topic}. "
        "Reply with one short sentence."
    )
    response = ask_gemini([prompt], search=True, thinking=THINKING_FOR_SHORT_REPLIES)
    links = grounding_links(response)
    if not links:
        raise AIError("No links found.")
    return {"links": links, "search_widget_html": search_widget(response)}


def grounding_links(response):
    """Title and link of each web source in the grounding metadata, without repeats."""
    links, seen = [], set()
    for candidate in response.candidates or []:
        metadata = candidate.grounding_metadata
        for chunk in (metadata.grounding_chunks or []) if metadata else []:
            uri = chunk.web.uri if chunk.web else None
            # Only plain web links go into an href (never javascript: or data:).
            if uri and uri.startswith(("https://", "http://")) and uri not in seen:
                seen.add(uri)
                links.append({"title": chunk.web.title or chunk.web.domain or uri, "uri": uri})
    return links[:5]


def search_widget(response):
    """The Google Search suggestions HTML that grounding returns (we must show it)."""
    for candidate in response.candidates or []:
        metadata = candidate.grounding_metadata
        if metadata and metadata.search_entry_point and metadata.search_entry_point.rendered_content:
            return metadata.search_entry_point.rendered_content
    return None


# ---------- Stand-ins for AI_ENABLED=false ----------
# They build cards only from the student's own notes or cards, so nothing is made up.

SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")
DEFINITION_PATTERN = re.compile(r"^(?P<term>[A-Z][\w\s()'-]{1,60}?)\s+(?:is|are|was|were|means)\s+(?P<rest>.{3,})$")

# Used when the stand-in gets a photo or PDF, which it can't read.
SAMPLE_NOTES_CARDS = [
    {"question": "Where does photosynthesis take place in a plant cell?", "answer": "In the chloroplasts.",
     "topic": "Photosynthesis", "importance": "high"},
    {"question": "What are the raw materials of photosynthesis?", "answer": "Carbon dioxide and water, using light energy.",
     "topic": "Photosynthesis", "importance": "high"},
    {"question": "Which gas is released during photosynthesis?", "answer": "Oxygen.",
     "topic": "Photosynthesis", "importance": "medium"},
    {"question": "Which pigment absorbs light for photosynthesis?", "answer": "Chlorophyll.",
     "topic": "Photosynthesis", "importance": "medium"},
]


def lower_first_letter(term):
    """'Osmosis' -> 'osmosis', but keep short forms like 'DNA' as they are."""
    if term.split()[0].isupper():
        return term
    return term[0].lower() + term[1:]


def cards_from_sentences(text):
    """'X is Y.' sentences become 'What is X?' cards. Everything comes from the notes."""
    cards = []
    for line in text.splitlines():
        for sentence in SENTENCE_PATTERN.split(line.strip()):
            sentence = sentence.strip()
            match = DEFINITION_PATTERN.match(sentence.rstrip("."))
            if not match:
                continue
            term, rest = match.group("term").strip(), match.group("rest").strip()
            verb = "are" if " are " in sentence else "is"
            cards.append({
                "question": f"What {verb} {lower_first_letter(term)}?",
                "answer": rest[0].upper() + rest[1:] + ("" if rest.endswith(".") else "."),
                "topic": "", "importance": "medium", "source_line": sentence,
            })
    return cards


def fake_make_cards(text, files):
    from app.cardmaker import quick_split  # the same rule the Quick split button uses

    cards = []
    if text:
        cards = quick_split(text) or cards_from_sentences(text)
    for number, file in enumerate(files, start=1):
        label = "PDF" if file["mime_type"] == "application/pdf" else f"photo {number}"
        sample = SAMPLE_NOTES_CARDS[(number - 1) % len(SAMPLE_NOTES_CARDS)]
        cards.append(dict(sample, source_line=f"From your {label}"))
    if not cards:
        raise AIError(COULD_NOT_READ)
    return clean_cards(cards)


def reversed_card(card):
    """Ask the other way round: the answer is on the front, recall the question. Always correct."""
    return {
        "question": card["answer"],
        "answer": card["question"],
        "topic": card.get("topic") or "",
        "importance": card.get("importance") or "medium",
        "source_line": "",
    }


def fake_generate_more(topics, existing_cards, count):
    already_reversed = {card["answer"] for card in existing_cards}
    cards = [reversed_card(card) for card in existing_cards if card["question"] not in already_reversed]
    if not cards:
        raise AIError("Add a few cards to this deck first, then try again.")
    return clean_cards(cards[:count])


def fake_explain(question, answer):
    return (
        f"This card asks: {question} The answer is: {answer} "
        "Try saying the answer in your own words before you flip, and link it to an example from class. "
        "If you mix this up with a similar idea, write both side by side and spot the difference."
    )


def fake_fix_weak_spot(topic, wrong_cards):
    if not wrong_cards:
        raise AIError(COULD_NOT_READ)
    # Three different ways to ask the missed cards again; with fewer than
    # 3 missed cards we still get 3 different practice cards.
    cards = [dict(card, topic=topic, importance="high") for card in wrong_cards]
    variants = (
        [reversed_card(card) for card in cards]
        + [dict(card, question=f"In one line: {card['question']}", source_line="") for card in cards]
        + [dict(card, question=f"Say it in your own words: {card['question']}", source_line="") for card in cards]
    )
    follow_up = variants[:3]
    explanation = (
        f"You keep missing cards in {topic}. Go slower on these: read the question, say your answer "
        "out loud, then check it against the back. Start with the cards you felt sure about but got "
        "wrong, because those are ideas you've learned the wrong way. The three practice cards below "
        "ask the same ideas from a different side."
    )
    return {"explanation": explanation, "follow_up_cards": clean_cards(follow_up, topic)}
