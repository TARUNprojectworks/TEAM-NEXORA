// Shared helpers for every page. Page-specific code goes in its own file
// (flip.js, study.js, cardmaker.js).

// Forms with data-confirm="..." ask before submitting (used for deletes).
document.addEventListener("submit", function (event) {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

// Forms with data-wait="..." show that message while the server works (AI calls can take
// up to ~20 s), and ignore a second click so one request isn't sent twice.
document.addEventListener("submit", function (event) {
  const form = event.target;
  if (!form.dataset.wait || event.defaultPrevented) return;
  if (form.dataset.busy) {
    event.preventDefault();
    return;
  }
  form.dataset.busy = "true";
  const status = form.querySelector(".wait-status");
  if (status) status.textContent = form.dataset.wait;
});

// POST JSON with the CSRF token in a header, as Flask-WTF expects.
// Returns the parsed JSON reply, or throws an Error with a readable message.
async function postJSON(url, data) {
  const token = document.querySelector('meta[name="csrf-token"]').content;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": token },
    body: JSON.stringify(data || {}),
  });
  const reply = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(reply.error || "Couldn't save that. Check your connection and try again.");
  }
  return reply;
}
