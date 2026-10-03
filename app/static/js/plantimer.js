// Soft time bar for today's plan. It only shows how long you've studied; it never stops
// the session. Elapsed seconds live in sessionStorage (one per plan day) so moving to the
// next card or reloading doesn't reset them, and the clock pauses while the tab is hidden.
(function () {
  const box = document.getElementById("plan-timer");
  if (!box) return;

  const limitMinutes = Number(box.dataset.minutes) || null;  // null = "No limit"
  const total = Number(box.dataset.total);
  const done = Number(box.dataset.done);
  const key = "nexora-plan-elapsed-" + box.dataset.date;
  const dismissedKey = key + "-keep-going";
  const hideKey = "nexora-hide-plan-timer";

  const fill = document.getElementById("timer-fill");
  const text = document.getElementById("timer-text");
  const doneNote = document.getElementById("timer-done");
  const doneText = document.getElementById("timer-done-text");
  const toggle = document.getElementById("timer-toggle");

  // Storage can be blocked (private windows); the timer then simply starts from 0.
  function read(store, name) {
    try { return store.getItem(name); } catch (error) { return null; }
  }
  function write(store, name, value) {
    try { store.setItem(name, value); } catch (error) { /* ignore */ }
  }

  let elapsed = Number(read(sessionStorage, key)) || 0;  // seconds

  function show() {
    const minutes = Math.floor(elapsed / 60);
    const cards = done + " of " + total + " cards";
    if (limitMinutes === null) {
      text.textContent = minutes + " min studied · " + cards;
      return;
    }
    const left = Math.max(Math.ceil((limitMinutes * 60 - elapsed) / 60), 0);
    text.textContent = left > 0
      ? minutes + " min done · about " + left + " min left · " + cards
      : minutes + " min done · " + cards;
    fill.style.width = Math.min(elapsed / (limitMinutes * 60), 1) * 100 + "%";
    if (left === 0 && !box.hidden && read(sessionStorage, dismissedKey) !== "yes") {
      doneText.textContent = limitMinutes + " minutes done. Finish this card, or keep going?";
      doneNote.hidden = false;
    }
  }

  // Count a second only while the student can see the page.
  setInterval(function () {
    if (document.visibilityState !== "visible") return;
    elapsed += 1;
    write(sessionStorage, key, String(elapsed));
    show();
  }, 1000);

  document.getElementById("keep-going").addEventListener("click", function () {
    write(sessionStorage, dismissedKey, "yes");
    doneNote.hidden = true;
  });

  // "Hide timer" is remembered across days (localStorage); the clock keeps counting either way.
  function applyHidden(hidden) {
    box.hidden = hidden;
    toggle.textContent = hidden ? "Show timer" : "Hide timer";
    if (hidden) doneNote.hidden = true;
  }
  toggle.addEventListener("click", function () {
    const hidden = !box.hidden;
    write(localStorage, hideKey, hidden ? "yes" : "no");
    applyHidden(hidden);
    if (!hidden) show();
  });

  applyHidden(read(localStorage, hideKey) === "yes");
  if (!box.hidden) show();
})();
