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

// A form with data-unsaved="message" asks before the student leaves with unsaved work:
// on the "← Back" link and on the browser's own back button (beforeunload).
// It counts as unsaved once something is typed, or from the start with data-dirty="true".
(function () {
  const form = document.querySelector("form[data-unsaved]");
  if (!form) return;
  let leaving = false;
  const unsaved = () => form.dataset.dirty === "true" && !leaving;

  form.addEventListener("input", function () { form.dataset.dirty = "true"; });
  form.addEventListener("submit", function () { leaving = true; });
  document.addEventListener("click", function (event) {
    const link = event.target.closest("a.back-link");
    if (!link || !unsaved()) return;
    if (window.confirm(form.dataset.unsaved)) {
      leaving = true;  // don't ask a second time in beforeunload
    } else {
      event.preventDefault();
    }
  });
  window.addEventListener("beforeunload", function (event) {
    if (!unsaved()) return;
    event.preventDefault();
    event.returnValue = "";  // browsers show their own "Leave site?" message
  });
})();

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

// ----- Side menu (☰): a modal <dialog>, so focus stays inside and Esc closes it -----
(function () {
  const drawer = document.getElementById("drawer");
  const openButton = document.getElementById("menu-button");
  if (!drawer || !openButton) return;
  function close() { drawer.close(); }
  openButton.addEventListener("click", function () {
    drawer.showModal();
    openButton.setAttribute("aria-expanded", "true");
  });
  drawer.addEventListener("close", function () {
    openButton.setAttribute("aria-expanded", "false");
    openButton.focus();
  });
  document.getElementById("drawer-close").addEventListener("click", close);
  drawer.addEventListener("click", function (event) {
    if (event.target === drawer) close();  // a click on the dimmed page beside the menu
  });
})();

// ----- Flash messages: close with ✕, or they fade on their own after 4 seconds -----
(function () {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll(".flash-list .flash").forEach(function (flash) {
    flash.querySelector(".flash-close").addEventListener("click", function () { flash.remove(); });
    setTimeout(function () {
      if (reduceMotion) { flash.remove(); return; }
      flash.classList.add("is-fading");
      setTimeout(function () { flash.remove(); }, 500);
    }, 4000);
  });
})();

// ----- Card theme pickers: the preview changes at once and the choice is saved -----
document.querySelectorAll("[data-theme-picker]").forEach(function (picker) {
  const status = picker.querySelector(".theme-status");
  picker.addEventListener("change", function (event) {
    const theme = event.target.value;
    // Every preview on the page follows (the menu and the card maker can both be open).
    document.querySelectorAll(".themed-card").forEach(function (card) {
      card.classList.remove("theme-index", "theme-clean", "theme-night");
      card.classList.add("theme-" + theme);
    });
    document.querySelectorAll('[data-theme-picker] input[value="' + theme + '"]').forEach(function (radio) {
      radio.checked = true;
    });
    status.textContent = "Saving...";
    postJSON(picker.dataset.url, { card_theme: theme })
      .then(function () { status.textContent = "Saved. Your cards use this theme."; })
      .catch(function (error) { status.textContent = error.message; });
  });
});

// ----- The ⋯ menu on deck cards (<details>): only one open at a time; a click outside or Esc closes it -----
document.addEventListener("click", function (event) {
  document.querySelectorAll("details.more-menu[open]").forEach(function (menu) {
    if (!menu.contains(event.target)) menu.removeAttribute("open");
  });
});
document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape") return;
  document.querySelectorAll("details.more-menu[open]").forEach(function (menu) {
    menu.removeAttribute("open");
    menu.querySelector("summary").focus();
  });
});
