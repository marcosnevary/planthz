// The polling replaces the tables every 2 seconds, so the history's scroll lives here.
let historyScrollTop = 0;

document.addEventListener("htmx:beforeSwap", () => {
  historyScrollTop = document.getElementById("history-list")?.scrollTop ?? 0;
});

document.addEventListener("htmx:afterSwap", () => {
  const list = document.getElementById("history-list");

  if (list) list.scrollTop = historyScrollTop;
});
