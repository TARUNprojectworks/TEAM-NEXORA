// AI Card Maker: small helpers for the maker form and the drafts page.
// Everything still works without JavaScript; this just makes it tidier.
(function () {
  // ----- Maker form -----
  const target = document.getElementById("target");
  const newDeckFields = document.getElementById("new-deck-fields");
  const typeLink = document.getElementById("type-link");

  function showTargetFields() {
    const isNew = target.value === "new";
    newDeckFields.hidden = !isNew;
    typeLink.href = isNew ? typeLink.dataset.newUrl : typeLink.dataset.deckUrl.replace("/0/", "/" + target.value + "/");
  }
  if (target && newDeckFields && typeLink) {
    target.addEventListener("change", showTargetFields);
    showTargetFields();
  }

  const fileInput = document.getElementById("files");
  const fileList = document.getElementById("file-list");
  if (fileInput && fileList) {
    fileInput.addEventListener("change", function () {
      fileList.replaceChildren();
      Array.from(fileInput.files).forEach(function (file) {
        const item = document.createElement("li");
        item.textContent = file.name + " (" + Math.max(1, Math.round(file.size / 1024)) + " KB)";
        fileList.appendChild(item);
      });
    });
  }

  // ----- Drafts page -----
  const list = document.getElementById("draft-list");
  const template = document.getElementById("draft-template");
  const count = document.getElementById("draft-count");
  if (!list || !template) return;

  function updateCount() {
    const n = list.children.length;
    count.textContent = n + (n === 1 ? " card" : " cards");
  }

  list.addEventListener("click", function (event) {
    if (!event.target.classList.contains("remove-draft")) return;
    event.target.closest(".draft").remove();
    updateCount();
  });

  document.getElementById("add-draft").addEventListener("click", function () {
    const row = template.content.firstElementChild.cloneNode(true);
    list.appendChild(row);
    row.querySelector("textarea").focus();
    updateCount();
  });
})();
