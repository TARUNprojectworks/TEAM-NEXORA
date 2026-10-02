// Load the Weak Spot Fixer's box after the page has shown (Gemini can take several seconds).
// The server sends the box as HTML it rendered itself (escaped by Jinja), or "" when there
// is no weak spot this time.
(function () {
  const slot = document.querySelector("[data-weak-spot-url]");
  if (!slot) return;

  const data = slot.dataset.topic ? { topic: slot.dataset.topic } : {};
  postJSON(slot.dataset.weakSpotUrl, data)
    .then(function (reply) {
      if (reply.html) {
        slot.innerHTML = reply.html;
      } else {
        slot.textContent = slot.dataset.emptyText || "";
        if (!slot.dataset.emptyText) slot.hidden = true;
      }
    })
    .catch(function () {
      slot.textContent = "Couldn't check for a weak spot right now. Try again after your next session.";
    });
})();
