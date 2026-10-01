// Flip the study card: click the card, press the Flip button, or press Space.
// Tells the page with a "card-flipped" event, so study.js can show the answer step.
(function () {
  const card = document.getElementById("study-card");
  if (!card) return;

  const button = document.getElementById("flip-button");
  const front = document.getElementById("card-front");
  const back = document.getElementById("card-back");

  function isTyping(event) {
    return event.target.matches("textarea, select, input[type=text], input[type=email]");
  }

  function flip() {
    const flipped = card.classList.toggle("is-flipped");
    // Screen readers should read only the side that is showing.
    front.setAttribute("aria-hidden", flipped ? "true" : "false");
    back.setAttribute("aria-hidden", flipped ? "false" : "true");
    card.dispatchEvent(new CustomEvent("card-flipped", { bubbles: true, detail: { flipped: flipped } }));
  }

  card.addEventListener("click", flip);
  button.addEventListener("click", flip);

  document.addEventListener("keydown", function (event) {
    if (event.key !== " " || isTyping(event)) return;
    // Space always flips here, even when a button has focus,
    // so it can never press "Know it" by accident.
    event.preventDefault();
    if (!event.repeat) flip();
  });
  document.addEventListener("keyup", function (event) {
    if (event.key === " " && !isTyping(event)) event.preventDefault();
  });
})();
