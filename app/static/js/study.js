// Study page: after the flip, pick Sure / Unsure, then Know it / Review again.
// Keys: S = Sure, U = Unsure, 1 = Know it, 2 = Review again.
(function () {
  const form = document.getElementById("answer-form");
  const card = document.getElementById("study-card");
  if (!form || !card) return;

  const knowButton = document.getElementById("know-button");
  const againButton = document.getElementById("again-button");
  const hint = document.getElementById("confidence-hint");

  const explainArea = document.getElementById("explain-area");
  const explainButton = document.getElementById("explain-button");
  const explanation = document.getElementById("explanation");

  // The answer step and Explain appear after the first flip and then stay.
  card.addEventListener("card-flipped", function () {
    form.hidden = false;
    if (explainArea) explainArea.hidden = false;
  });

  // Explain this card: the server returns plain text, shown with textContent (never as HTML).
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

  function enableAnswerButtons() {
    knowButton.disabled = false;
    againButton.disabled = false;
    hint.hidden = true;
  }

  form.querySelectorAll('input[name="confident"]').forEach(function (radio) {
    radio.addEventListener("change", enableAnswerButtons);
  });

  function chooseConfidence(value) {
    form.querySelector('input[name="confident"][value="' + value + '"]').checked = true;
    enableAnswerButtons();
  }

  function answer(button) {
    if (button.disabled) {
      hint.textContent = "Pick Sure or Unsure first (S or U).";
      return;
    }
    form.requestSubmit(button);
  }

  document.addEventListener("keydown", function (event) {
    if (form.hidden || event.ctrlKey || event.metaKey || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key === "s") chooseConfidence("sure");
    else if (key === "u") chooseConfidence("unsure");
    else if (key === "1") answer(knowButton);
    else if (key === "2") answer(againButton);
  });
})();
