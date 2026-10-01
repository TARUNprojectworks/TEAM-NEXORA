// Card form: show a gentle "looks long" hint while typing. It never blocks saving.
(function () {
  const form = document.getElementById("card-form");
  if (!form) return;

  const front = document.getElementById("question");
  const back = document.getElementById("answer");
  const warning = document.getElementById("long-warning");
  const longFront = Number(form.dataset.longFront);
  const longBack = Number(form.dataset.longBack);

  function checkLength() {
    const tooLong = front.value.trim().length > longFront || back.value.trim().length > longBack;
    warning.hidden = !tooLong;
  }

  front.addEventListener("input", checkLength);
  back.addEventListener("input", checkLength);
  checkLength();
})();
