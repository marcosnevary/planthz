// Legend toggles, tooltips and drag-to-zoom on the analysis charts, whose SVG is rendered by charts.py.
(() => {
  const DRAG_THRESHOLD = 4;
  const hiddenPlants = new Map();
  let drag = null;
  let suppressClick = false;
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  document.body.append(tip);

  const panelIndex = (panel) => [...document.querySelectorAll(".chart-panel")].indexOf(panel);

  // A zoom replaces the panels, so the hidden plants are kept here and applied again after each swap.
  const applyHiddenPlants = () => {
    document.querySelectorAll(".chart-panel").forEach((panel) => {
      const hidden = hiddenPlants.get(panelIndex(panel)) ?? new Set();

      panel.querySelectorAll(".legend-toggle").forEach((button) => {
        button.setAttribute("aria-pressed", String(!hidden.has(button.dataset.plant)));
      });
      panel.querySelectorAll(".series").forEach((series) => {
        series.toggleAttribute("hidden", hidden.has(series.dataset.plant));
      });
    });
  };

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".legend-toggle");

    if (!button) return;

    const index = panelIndex(button.closest(".chart-panel"));
    const hidden = hiddenPlants.get(index) ?? new Set();
    hidden.has(button.dataset.plant) ? hidden.delete(button.dataset.plant) : hidden.add(button.dataset.plant);
    hiddenPlants.set(index, hidden);
    applyHiddenPlants();
  });

  document.addEventListener("htmx:afterSettle", applyHiddenPlants);

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

    if (target && !drag) showTip(target, event.clientX, event.clientY);
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

  const svgX = (svg, clientX) => {
    const point = new DOMPoint(clientX, 0).matrixTransform(svg.getScreenCTM().inverse());
    const left = Number(svg.dataset.plotLeft);
    const right = Number(svg.dataset.plotRight);

    return Math.min(Math.max(point.x, left), right);
  };

  const dayOffset = (svg, x) => {
    const left = Number(svg.dataset.plotLeft);
    const right = Number(svg.dataset.plotRight);
    const days = Number(svg.dataset.days);

    return Math.min(Math.floor(((x - left) / (right - left)) * days), days - 1);
  };

  const isoDay = (svg, offset) => {
    const day = new Date(`${svg.dataset.start}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() + offset);

    return day.toISOString().slice(0, 10);
  };

  document.addEventListener("pointerdown", (event) => {
    const svg = event.target.closest?.("svg.chart");

    if (!svg || event.button !== 0) return;

    const selection = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    selection.setAttribute("class", "selection");
    selection.setAttribute("y", "0");
    selection.setAttribute("height", "100%");
    drag = { svg, selection, clientX: event.clientX, from: svgX(svg, event.clientX), moved: false };
  });

  document.addEventListener("pointermove", (event) => {
    if (!drag) return;

    if (!drag.moved && Math.abs(event.clientX - drag.clientX) < DRAG_THRESHOLD) return;

    if (!drag.moved) {
      drag.moved = true;
      drag.svg.append(drag.selection);
    }

    const to = svgX(drag.svg, event.clientX);
    drag.selection.setAttribute("x", String(Math.min(drag.from, to)));
    drag.selection.setAttribute("width", String(Math.abs(to - drag.from)));
    drag.to = to;
  });

  document.addEventListener("pointerup", () => {
    if (!drag) return;

    const { svg, selection, from, to, moved } = drag;
    drag = null;

    if (!moved) return;

    selection.remove();
    suppressClick = true;
    setTimeout(() => {
      suppressClick = false;
    });

    const offsets = [dayOffset(svg, from), dayOffset(svg, to)].sort((a, b) => a - b);
    const content = document.getElementById("analysis-content");
    const url = `${content.dataset.url}?start=${isoDay(svg, offsets[0])}&end=${isoDay(svg, offsets[1])}`;

    htmx
      .ajax("GET", url, { target: "#analysis-content", select: "#analysis-content", swap: "outerHTML" })
      .then(() => history.replaceState(history.state, "", url));
  });

  // After a drag that started on a point, the browser still fires a click that would open the video.
  document.addEventListener(
    "click",
    (event) => {
      if (suppressClick && event.target.closest("svg.chart")) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true,
  );

  // An SVG link would otherwise start a native link drag and swallow the pointer events.
  document.addEventListener("dragstart", (event) => {
    if (event.target.closest?.("svg.chart")) event.preventDefault();
  });
})();
