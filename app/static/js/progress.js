// My Progress: "Explain" next to each misconception card. The server returns plain text,
// shown with textContent (never as HTML). Explanations are cached, so a second tap is free.
document.querySelectorAll(".explain-row").forEach(function (button) {
  button.addEventListener("click", function () {
    const output = button.closest("li").querySelector(".explanation");
    button.disabled = true;
    output.textContent = "Getting an explanation...";
    postJSON(button.dataset.url)
      .then(function (reply) {
        output.textContent = reply.explanation;
        button.hidden = true;
      })
      .catch(function (error) {
        output.textContent = error.message;
        button.disabled = false;
      });
  });
});
