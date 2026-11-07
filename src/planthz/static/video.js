// Frame slider and left/right arrow navigation between videos on the video page.
(() => {
  const frame = document.getElementById("video-frame");
  const position = document.getElementById("video-position");
  const positionValue = document.getElementById("video-position-value");

  const showPosition = () => {
    const percent = `${(position.value / position.max) * 100}%`;
    positionValue.textContent = percent;
    position.style.setProperty("--fill", percent);
  };

  showPosition();
  position.addEventListener("input", showPosition);

  position.addEventListener("change", () => {
    frame.src = `/videos/${position.dataset.video}/frame.jpg?position=${position.value}`;
  });

  document.addEventListener("keydown", (event) => {
    // On the slider the arrows move the slider, not the page.
    if (event.target === position) return;

    const link = { ArrowLeft: "previous-video", ArrowRight: "next-video" }[event.key];
    const target = link && document.getElementById(link);

    if (target) target.click();
  });
})();
