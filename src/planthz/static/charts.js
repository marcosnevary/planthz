// Legend toggles for each plant and the standard deviation band, and tooltips on the analysis charts, whose SVG is rendered by charts.py.
(() => {
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  document.body.append(tip);

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".legend-toggle");

    if (!button) return;

    const visible = button.getAttribute("aria-pressed") !== "true";
    const selector = "band" in button.dataset ? ".band" : `.series[data-plant="${button.dataset.plant}"]`;
    button.setAttribute("aria-pressed", String(visible));
    button
      .closest(".chart-panel")
      .querySelectorAll(selector)
      .forEach((element) => element.toggleAttribute("hidden", !visible));
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
