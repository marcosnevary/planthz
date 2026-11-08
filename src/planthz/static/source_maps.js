// Heatmap of each plant's selected source over the frame, static or pulsing with the source signal.
(() => {
  // The same threshold and colormap as marajo's create_*_source_overlay_video.
  const THRESHOLD = 0.15;
  const frame = document.getElementById("video-frame");

  const jet = (value) => {
    const channel = (offset) => Math.round(255 * Math.min(Math.max(1.5 - Math.abs(4 * value - offset), 0), 1));
    return [channel(3), channel(2), channel(1)];
  };

  const formatFrequency = (value) =>
    value.toLocaleString("pt-BR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });

  const placeOver = (canvas) => {
    if (!frame.naturalWidth) return;

    // The ROI is in video pixels, and a frame kept after the video left the disk can be smaller than the video.
    const width = Number(frame.dataset.width) || frame.naturalWidth;
    const height = Number(frame.dataset.height) || frame.naturalHeight;
    const percent = (value, total) => `${(value / total) * 100}%`;
    canvas.style.left = percent(canvas.dataset.x, width);
    canvas.style.top = percent(canvas.dataset.y, height);
    canvas.style.width = percent(canvas.dataset.w, width);
    canvas.style.height = percent(canvas.dataset.h, height);
  };

  const loadWeights = async (url) => {
    const image = new Image();
    image.src = url;
    await image.decode();

    const scratch = document.createElement("canvas");
    scratch.width = image.width;
    scratch.height = image.height;
    const context = scratch.getContext("2d");
    context.drawImage(image, 0, 0);

    const pixels = context.getImageData(0, 0, image.width, image.height).data;
    const weights = new Float32Array(image.width * image.height);

    for (let i = 0; i < weights.length; i += 1) weights[i] = pixels[4 * i] / 255;

    return { weights, width: image.width, height: image.height };
  };

  const setUp = async (origin) => {
    const { video, plant } = origin.dataset;
    const state = origin.querySelector(".source-origin-state");
    const controls = origin.querySelector(".source-origin-controls");
    const select = controls.querySelector("select");
    const visible = controls.querySelector("input[type=checkbox]");
    const animate = controls.querySelector("button");
    const canvas = document.getElementById(`source-map-${plant}`);
    const context = canvas.getContext("2d");

    const response = await fetch(`/videos/${video}/plants/${plant}/sources.json`);

    if (!response.ok) {
      state.textContent = "O mapa das fontes não está disponível: o vídeo não está no disco.";
      return;
    }

    const { fps, dominant, sources } = await response.json();

    for (const source of sources) {
      const label = `Fonte ${source.index} · ${formatFrequency(source.frequency)} Hz`;
      select.add(new Option(source.index === dominant ? `${label} (dominante)` : label, source.index));
    }

    select.value = dominant ?? 0;

    let current = null;
    let animation = null;

    const draw = (amplitude) => {
      const { weights, width, height } = current.map;
      const image = context.createImageData(width, height);

      for (let i = 0; i < weights.length; i += 1) {
        const mask = Math.min(Math.max((weights[i] - THRESHOLD) / (1 - THRESHOLD), 0), 1);
        const [red, green, blue] = jet(weights[i] * amplitude);
        image.data.set([red, green, blue, Math.round(255 * mask * amplitude)], 4 * i);
      }

      context.putImageData(image, 0, 0);
    };

    const stop = () => {
      if (animation !== null) cancelAnimationFrame(animation);
      animation = null;
      animate.textContent = "Animar";
      if (current) draw(1);
    };

    const play = () => {
      const start = performance.now();
      const signal = current.source.signal;

      const step = (now) => {
        draw(signal[Math.floor(((now - start) / 1000) * fps) % signal.length]);
        animation = requestAnimationFrame(step);
      };

      animate.textContent = "Parar";
      animation = requestAnimationFrame(step);
    };

    const show = async () => {
      stop();
      const index = Number(select.value);
      const map = await loadWeights(`/videos/${video}/plants/${plant}/sources/${index}.png`);

      // A later selection may have finished first.
      if (Number(select.value) !== index) return;

      current = { source: sources[index], map };
      canvas.width = map.width;
      canvas.height = map.height;
      draw(1);
    };

    select.addEventListener("change", show);
    visible.addEventListener("change", () => {
      canvas.hidden = !visible.checked;
      if (!visible.checked) stop();
    });
    animate.addEventListener("click", () => {
      if (animation !== null) return stop();

      visible.checked = true;
      canvas.hidden = false;
      play();
    });

    placeOver(canvas);
    frame.addEventListener("load", () => placeOver(canvas));

    await show();
    canvas.hidden = false;
    state.hidden = true;
    controls.hidden = false;
  };

  for (const origin of document.querySelectorAll(".source-origin")) {
    setUp(origin).catch(() => {
      origin.querySelector(".source-origin-state").textContent = "Não foi possível carregar o mapa das fontes.";
    });
  }
})();
