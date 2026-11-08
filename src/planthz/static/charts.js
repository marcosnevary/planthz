// Legend toggles and tooltips on the analysis charts, whose SVG is rendered by charts.py.
(() => {
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  document.body.append(tip);

  document.addEventListener("click", (event) => {
    const modeButton = event.target.closest(".mode-toggle");

    if (!modeButton) return;

    const panel = modeButton.closest(".chart-panel");
    const mode = modeButton.dataset.mode;
    panel.querySelectorAll(".mode-toggle").forEach((button) => {
      button.setAttribute("aria-pressed", String(button === modeButton));
    });
    panel.querySelector(".chart").dataset.mode = mode;
    panel.querySelector(".moving-average").hidden = mode !== "line";
  });

  document.addEventListener("change", (event) => {
    const select = event.target.closest(".moving-average select");

    if (!select) return;

    const chart = select.closest(".chart-panel").querySelector(".chart");
    chart.toggleAttribute("data-average", select.value !== "");
    chart.querySelectorAll(".average").forEach((line) => {
      line.toggleAttribute("hidden", line.dataset.window !== select.value);
    });
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".legend-toggle");

    if (!button) return;

    const visible = button.getAttribute("aria-pressed") !== "true";
    const panel = button.closest(".chart-panel");
    button.setAttribute("aria-pressed", String(visible));

    if ("band" in button.dataset) {
      panel.querySelector(".chart").toggleAttribute("data-without-band", !visible);
      return;
    }

    // The compared campaign has its own toggles, so a plant's button leaves the other campaign's series alone.
    const compared = "compared" in button.dataset;
    panel
      .querySelectorAll(`.series[data-plant="${button.dataset.plant}"]`)
      .forEach((series) => {
        if ("compared" in series.dataset === compared) series.toggleAttribute("hidden", !visible);
      });
  });

  const showTip = (target, x, y) => {
    tip.textContent = target.dataset.tip;
    tip.hidden = false;
    // Kept inside the window, so a point near the right edge does not push its tooltip off screen.
    const left = Math.min(x + 12, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = `${left}px`;
    tip.style.top = `${y + 16}px`;
  };

  document.addEventListener("pointermove", (event) => {
    const target = event.target.closest?.(".chart [data-tip]");

    if (target) showTip(target, event.clientX, event.clientY);
    else tip.hidden = true;
  });

  document.addEventListener("focusin", (event) => {
    const target = event.target.closest?.(".chart [data-tip]");

    if (!target) return;

    const box = target.getBoundingClientRect();
    showTip(target, box.right, box.bottom);
  });

  document.addEventListener("focusout", () => {
    tip.hidden = true;
  });
})();
