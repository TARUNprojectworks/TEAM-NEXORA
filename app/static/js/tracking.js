// Tracking page: draw the charts from /tracking/data. Every number is
// also in a table on the page, so the charts are a picture, not the only copy.
(function () {
  const script = document.getElementById("tracking-script");
  if (!script || typeof Chart === "undefined") return;

  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  const ink = color("--ink");
  const inkSoft = color("--ink-soft");
  const green = color("--know-green");
  const line = color("--line");
  // Shelves 1-4 in steps of navy, shelf 5 (learned) in green.
  const shelfColors = ["#B9C6D0", "#8EA2B3", "#5F7A91", "#34506A", green];

  Chart.defaults.font.family = css.getPropertyValue("--font-ui");
  Chart.defaults.color = inkSoft;
  Chart.defaults.borderColor = line;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    Chart.defaults.animation = false;
  }

  function masteryChart(rows) {
    const canvas = document.getElementById("mastery-chart");
    if (!canvas) return;
    new Chart(canvas, {
      type: "bar",
      data: {
        labels: rows.map((row) => row.topic + " (" + row.deck + ")"),
        datasets: [{
          label: "Mastery %",
          data: rows.map((row) => row.mastery),
          backgroundColor: rows.map((row) => (row.mastery === 100 ? green : ink)),
          barThickness: 16,
        }],
      },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        scales: { x: { min: 0, max: 100, ticks: { callback: (value) => value + "%" } } },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (item) => {
            const row = rows[item.dataIndex];
            return row.mastery + "% (" + row.learned + " of " + row.total + " cards on shelf 5)";
          } } },
        },
      },
    });
  }

  function deckChart(rows) {
    const canvas = document.getElementById("deck-chart");
    if (!canvas) return;
    const datasets = [{ label: "Not studied", data: rows.map((row) => row.new), backgroundColor: "#E9EEF2" }];
    for (let shelf = 0; shelf < 5; shelf++) {
      datasets.push({
        label: "Shelf " + (shelf + 1),
        data: rows.map((row) => row.shelves[shelf]),
        backgroundColor: shelfColors[shelf],
      });
    }
    new Chart(canvas, {
      type: "bar",
      data: { labels: rows.map((row) => row.deck), datasets: datasets },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        scales: { x: { stacked: true, title: { display: true, text: "Cards" } }, y: { stacked: true } },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 14 } } },
      },
    });
  }

  fetch(script.dataset.url, { headers: { Accept: "application/json" } })
    .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
    .then((data) => {
      masteryChart(data.mastery);
      deckChart(data.decks);
    })
    .catch(() => {
      // The tables below each chart still show every number.
    });
})();
