// Study page: after the flip, pick Sure / Unsure, then Know it / Review again.
// Keys: S = Sure, U = Unsure, 1 = Know it, 2 = Review again.
(function () {
  const form = document.getElementById("answer-form");
  const card = document.getElementById("study-card");
  if (!form || !card) return;

  const knowButton = document.getElementById("know-button");
  const againButton = document.getElementById("again-button");
  const hint = document.getElementById("confidence-hint");

  // The answer step appears after the first flip and then stays.
  card.addEventListener("card-flipped", function () {
    form.hidden = false;
  });

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
