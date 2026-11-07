// Draws both plants' rectangles over the frame. The number inputs hold the state, in video pixels,
// so what is submitted is always what is on screen.
(() => {
  const stage = document.getElementById("roi-stage");

  if (!stage) return;

  const frame = document.getElementById("roi-frame");
  const overlay = document.getElementById("roi-overlay");
  const positionInput = document.getElementById("roi-position");
  const positionValue = document.getElementById("roi-position-value");
  const form = document.getElementById("roi-form");

  const input = (plant, axis) => form.querySelector(`input[data-plant="${plant}"][data-axis="${axis}"]`);
  const activePlant = () => form.querySelector('input[name="active_plant"]:checked').value;
  const clamp = (value, max) => Math.min(Math.max(value, 0), max);

  function render() {
    for (const group of overlay.querySelectorAll("g")) {
      const plant = group.dataset.plant;
      const [x, y, w, h] = ["x", "y", "w", "h"].map((axis) => Number(input(plant, axis).value) || 0);
      const rect = group.querySelector("rect");
      const label = group.querySelector("text");

      group.style.display = w > 0 && h > 0 ? "" : "none";
      rect.setAttribute("x", x);
      rect.setAttribute("y", y);
      rect.setAttribute("width", w);
      rect.setAttribute("height", h);
      label.setAttribute("x", x + 12);
      label.setAttribute("y", y + 36);
    }
  }

  function toVideoPixels(event) {
    const box = frame.getBoundingClientRect();

    return {
      x: clamp(Math.round(((event.clientX - box.left) / box.width) * frame.naturalWidth), frame.naturalWidth),
      y: clamp(Math.round(((event.clientY - box.top) / box.height) * frame.naturalHeight), frame.naturalHeight),
    };
  }

  function setRectangle(plant, start, end) {
    input(plant, "x").value = Math.min(start.x, end.x);
    input(plant, "y").value = Math.min(start.y, end.y);
    input(plant, "w").value = Math.abs(end.x - start.x);
    input(plant, "h").value = Math.abs(end.y - start.y);
    render();
  }

  let dragStart = null;

  stage.addEventListener("pointerdown", (event) => {
    if (!frame.naturalWidth) return;

    dragStart = toVideoPixels(event);
    stage.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  stage.addEventListener("pointermove", (event) => {
    if (dragStart) setRectangle(activePlant(), dragStart, toVideoPixels(event));
  });

  const endDrag = () => {
    dragStart = null;
  };

  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);

  form.addEventListener("input", render);

  frame.addEventListener("load", () => {
    overlay.setAttribute("viewBox", `0 0 ${frame.naturalWidth} ${frame.naturalHeight}`);
    render();
  });

  if (frame.complete && frame.naturalWidth) {
    overlay.setAttribute("viewBox", `0 0 ${frame.naturalWidth} ${frame.naturalHeight}`);
    render();
  }

  const showPosition = () => {
    const percent = `${(positionInput.value / positionInput.max) * 100}%`;
    positionValue.textContent = percent;
    positionInput.style.setProperty("--fill", percent);
  };

  new ResizeObserver(() => {
    form.style.minHeight = `${stage.offsetHeight}px`;
  }).observe(stage);

  const saveButton = form.querySelector(".save-button");

  if ("saved" in saveButton.dataset) {
    const url = new URL(location.href);
    url.searchParams.delete("saved");
    history.replaceState(null, "", url);

    // Waiting a frame lets the page paint the idle label first, so the change to the message animates.
    requestAnimationFrame(() => requestAnimationFrame(() => saveButton.classList.add("showing-saved")));
    setTimeout(() => saveButton.classList.remove("showing-saved"), 2500);
  }

  showPosition();
  positionInput.addEventListener("input", showPosition);

  positionInput.addEventListener("change", () => {
    frame.src = `/videos/${positionInput.dataset.video}/frame.jpg?overlay=false&position=${positionInput.value}`;
  });
})();
