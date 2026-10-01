// Shared helpers for every page. Page-specific code goes in its own file
// (flip.js, study.js, cardmaker.js).

// Forms with data-confirm="..." ask before submitting (used for deletes).
document.addEventListener("submit", function (event) {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
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
