"""Every Gemini call lives here and nowhere else.

Owner: AI. Built in phase 5 (fake mode) and phase 6 (real Gemini on Vertex AI).
When AI_ENABLED is false, each function returns realistic fake data with
exactly the same shape as the real call.

Planned public functions:
    make_cards(text=None, image_bytes=None, image_uri=None)
        -> [{"question", "answer", "topic", "importance", "source_line"}, ...]
    explain_card(card)            -> str (3-4 plain sentences)
    fix_weak_spot(topic, wrong_cards)
        -> {"explanation": str, "follow_up_cards": [3 card dicts]}
    find_resources(topic)
        -> {"links": [{"title", "uri"}], "search_widget_html": str or None}

Rules: 20 calls per student per day (Explain from cache doesn't count),
15 s timeout, temperature about 0.3, log feature + latency_ms + ok/error
only, never send names, emails or passwords to Gemini.
"""

import logging

log = logging.getLogger("nexora.ai")


class AIError(Exception):
    """Raised when Gemini fails or returns something we can't read."""
