// Study page, in this order:
//   1. read the question and think of the answer
//   2. "I'm confident" or "Not sure"  (keys 1 / 2)   -> sent as confident = sure / unsure
//   3. flip the card                  (Space)
//   4. "I got it right" or "I missed it" (keys 1 / 2) -> sent as knew_it = 1 / 0
// The engine gets exactly what it got before (Sure/Unsure, Know it/Review again);
// only the order on screen changed, so confidence is given before seeing the answer.
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
  const youSaid = document.getElementById("you-said");
  const knowButton = document.getElementById("know-button");
  const againButton = document.getElementById("again-button");
  const misconceptionNote = document.getElementById("misconception-note");
  const explainArea = document.getElementById("explain-area");
  const explainButton = document.getElementById("explain-button");
  const explanation = document.getElementById("explanation");
  let flippedOnce = false;

  // ----- Step 2: confidence, before the flip -----
  function chooseConfidence(button) {
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

  // ----- Step 4: after the first flip, ask if they got it right -----
  card.addEventListener("card-flipped", function () {
    if (flippedOnce) return;
    flippedOnce = true;
    stepConfidence.hidden = true;
    youSaid.textContent = "You said: " + (confidentInput.value === "sure" ? "I'm confident" : "Not sure");
    stepAnswer.hidden = false;
    if (explainArea) explainArea.hidden = false;
  });

  // Confident + missed is a misconception. Stop once on this card so the student
  // can tap Explain; pressing "I missed it" (now "Next card") again moves on.
  let misconceptionShown = false;
  form.addEventListener("submit", function (event) {
    const confident = confidentInput.value === "sure";
    if (event.submitter !== againButton || !confident || misconceptionShown) return;
    event.preventDefault();
    misconceptionShown = true;
    misconceptionNote.hidden = false;
    againButton.textContent = "Next card";
    if (explainButton && !explainButton.hidden) explainButton.focus();
  });

  // ----- Keys: 1 / 2 press whichever pair is showing -----
  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key !== "1" && event.key !== "2") return;
    const first = event.key === "1";
    if (!flippedOnce) {
      chooseConfidence(first ? confidentButton : unsureButton);
    } else {
      form.requestSubmit(first ? knowButton : againButton);
    }
  });

  // ----- The small "Moved to Good" tag from the last answer fades after about a second -----
  const tag = document.getElementById("answer-tag");
  if (tag) {
    setTimeout(function () { tag.classList.add("is-fading"); }, 1000);
    setTimeout(function () { tag.hidden = true; }, 1400);
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
