// Study page, in this order:
//   1. read the question and think of the answer
//   2. "Confident" or "Not sure"  (keys 1 / 2)   -> sent as confident = sure / unsure
//   3. flip the card              (Space)
//   4. the answer pair for that choice (keys 1 / 2) -> sent as knew_it = 1 / 0
//        Confident: "Got it right" / "Got it wrong"
//        Not sure:  "Recalled it"  / "I didn't get it"
// The engine gets exactly what it got before (Sure/Unsure, Know it/Review again);
// only the words on screen depend on the first choice.
(function () {
  const form = document.getElementById("answer-form");
  const card = document.getElementById("study-card");
  if (!form || !card) return;

  const confidentInput = document.getElementById("confident-input");
  const confidentButton = document.getElementById("confident-button");
  const unsureButton = document.getElementById("unsure-button");
  const pickFirst = document.getElementById("pick-first");
  const flipRow = document.getElementById("flip-row");
  const stepConfidence = document.getElementById("step-confidence");
  const stepAnswer = document.getElementById("step-answer");
  const tag = document.getElementById("answer-tag");
  const nextCard = document.getElementById("next-card");
  const explainArea = document.getElementById("explain-area");
  const explainButton = document.getElementById("explain-button");
  const explanation = document.getElementById("explanation");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let flippedOnce = false;
  let answered = false;

  // The short note shown for about a second after answering.
  const TAGS = {
    moved: stepAnswer.dataset.canMoveUp === "true" ? "Moved up" : "Got it.",
    misconception: "You were confident about this one. Tap Explain.",
    recalled: "Good recall. You'll see it again soon to lock it in.",
    missed: "No problem. This one comes back soon.",
  };

  // ----- Step 2: confidence, before the flip -----
  function chooseConfidence(button) {
    if (flippedOnce) return;
    confidentInput.value = button.dataset.value;
    [confidentButton, unsureButton].forEach(function (b) {
      b.setAttribute("aria-pressed", b === button ? "true" : "false");
    });
    card.dataset.locked = "false";
    pickFirst.hidden = true;
    flipRow.hidden = false;
    document.getElementById("flip-button").focus();
  }
  confidentButton.addEventListener("click", function () { chooseConfidence(confidentButton); });
  unsureButton.addEventListener("click", function () { chooseConfidence(unsureButton); });

  card.addEventListener("flip-blocked", function () {
    pickFirst.hidden = false;
  });

  function visiblePair() {
    return stepAnswer.querySelector('[data-pair="' + confidentInput.value + '"]');
  }

  // ----- Step 4: after the first flip, show the pair that matches the first choice -----
  card.addEventListener("card-flipped", function () {
    if (flippedOnce) return;
    flippedOnce = true;
    stepConfidence.hidden = true;
    stepAnswer.hidden = false;
    visiblePair().hidden = false;
    if (explainArea) explainArea.hidden = false;
  });

  // Show the tag for about a second, then send the answer. "Got it wrong" (a misconception)
  // waits instead, so the student can tap Explain; "Next card" sends it.
  form.addEventListener("submit", function (event) {
    const button = event.submitter;
    if (!button || !button.dataset.tag || answered) return;
    event.preventDefault();
    answered = true;
    stepAnswer.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    tag.textContent = TAGS[button.dataset.tag];
    tag.classList.toggle("is-misconception", button.dataset.tag === "misconception");
    tag.hidden = false;
    if (button.dataset.tag === "misconception") {
      stepAnswer.hidden = true;
      nextCard.hidden = false;
      if (explainButton && !explainButton.hidden) explainButton.focus();
      return;
    }
    const knewIt = button.value;
    setTimeout(function () {
      // A plain hidden input, because a button disabled above isn't sent with the form.
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "knew_it";
      input.value = knewIt;
      form.appendChild(input);
      form.submit();
    }, reduceMotion ? 600 : 1000);
  });

  // ----- Keys: 1 / 2 press whichever pair is showing -----
  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key !== "1" && event.key !== "2") return;
    if (document.querySelector("dialog[open]")) return;
    const first = event.key === "1";
    if (!flippedOnce) {
      chooseConfidence(first ? confidentButton : unsureButton);
    } else if (!answered) {
      const buttons = visiblePair().querySelectorAll("button");
      form.requestSubmit(buttons[first ? 0 : 1]);
    }
  });

  // ----- End session / Back: ask first -----
  const endDialog = document.getElementById("end-dialog");
  if (endDialog) {
    const endConfirm = document.getElementById("end-confirm");
    document.addEventListener("click", function (event) {
      const link = event.target.closest("a[data-end-session]");
      if (!link) return;
      event.preventDefault();
      // "End" goes where the clicked link was going (summary, or the deck for Back).
      endConfirm.href = link.href;
      endDialog.showModal();
      document.getElementById("end-cancel").focus();
    });
    document.getElementById("end-cancel").addEventListener("click", function () { endDialog.close(); });
    endDialog.addEventListener("click", function (event) {
      if (event.target === endDialog) endDialog.close();  // click on the backdrop
    });
  }

  // ----- Explain this card: plain text from the server, shown with textContent -----
  if (explainButton) {
    explainButton.addEventListener("click", function () {
      explainButton.disabled = true;
      explanation.textContent = "Getting an explanation...";
      postJSON(explainButton.dataset.url)
        .then(function (reply) {
          explanation.textContent = reply.explanation;
          explainButton.hidden = true;
        })
        .catch(function (error) {
          explanation.textContent = error.message;
          explainButton.disabled = false;
        });
    });
  }
})();
