// The polling replaces the tables every 2 seconds, so the selected tab and the history's scroll live here.
let selectedTab = "tab-days";
let historyScrollTop = 0;

function showSelectedTab() {
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    const selected = tab.id === selectedTab;

    tab.setAttribute("aria-selected", selected);
    document.getElementById(tab.getAttribute("aria-controls")).hidden = !selected;
  }
}

document.addEventListener("click", (event) => {
  const tab = event.target.closest('[role="tab"]');

  if (tab) {
    selectedTab = tab.id;
    showSelectedTab();
  }
});

document.addEventListener("htmx:beforeSwap", () => {
  historyScrollTop = document.getElementById("history-list")?.scrollTop ?? 0;
});

document.addEventListener("htmx:afterSwap", () => {
  showSelectedTab();

  const list = document.getElementById("history-list");

  if (list) list.scrollTop = historyScrollTop;
});
