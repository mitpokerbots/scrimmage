// Progressive enhancement only; every page works without JavaScript.
document.addEventListener("submit", (event) => {
  const form = event.target;
  const question = form.dataset.confirm;
  if (question && !window.confirm(question)) {
    event.preventDefault();
    return;
  }
  // Prevent double submits (double challenges, double uploads).
  for (const button of form.querySelectorAll("button")) {
    button.disabled = true;
    if (form.hasAttribute("data-upload")) button.textContent = "Uploading…";
  }
});

// Pages restored from the back/forward cache keep disabled buttons otherwise.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  for (const button of document.querySelectorAll("form button[disabled]")) {
    button.disabled = false;
  }
});

// The off-season countdown ticks every second, and reloads when the site opens.
const countdown = document.querySelector("[data-countdown]");
if (countdown) {
  const opensAt = Number(countdown.dataset.countdown) * 1000;
  const units = { days: 86400, hours: 3600, minutes: 60, seconds: 1 };
  const tick = () => {
    let left = Math.max(0, Math.floor((opensAt - Date.now()) / 1000));
    if (left === 0) {
      window.location.reload();
      return;
    }
    for (const [unit, size] of Object.entries(units)) {
      const cell = countdown.querySelector(`[data-unit="${unit}"]`);
      const text = String(Math.floor(left / size)).padStart(2, "0");
      left %= size;
      if (cell.textContent === text) continue;
      cell.textContent = text;
      cell.classList.remove("tick");
      void cell.offsetWidth; // restart the animation
      cell.classList.add("tick");
    }
  };
  tick();
  setInterval(tick, 1000);
}

// Step through a finished hand. Without this script the whole hand stays visible.
const replay = document.querySelector("[data-replay]");
if (replay) {
  const beats = [...replay.querySelectorAll("[data-beat]")];
  const controls = replay.querySelector("[data-replay-controls]");
  const status = replay.querySelector("[data-replay-status]");
  if (beats.length > 0 && controls instanceof HTMLElement) {
    controls.hidden = false;
    let cursor = beats.length;
    const paint = () => {
      const live = cursor < beats.length;
      replay.classList.toggle("is-replaying", live);
      beats.forEach((beat, index) => beat.classList.toggle("is-future", index >= cursor));
      if (status) status.textContent = live ? `Step ${cursor} of ${beats.length}` : "";
    };
    controls.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const action = target.closest("[data-replay-action]");
      if (!(action instanceof HTMLElement)) return;
      const name = action.dataset.replayAction;
      if (name === "start") cursor = 1;
      else if (name === "prev") cursor = Math.max(1, cursor - 1);
      else if (name === "next") cursor = Math.min(beats.length, cursor + 1);
      else if (name === "all") cursor = beats.length;
      else return;
      paint();
    });
  }
}

// Refresh marked regions from the same page about once a second.
// Skips a region the pointer or keyboard is in, so a click or a form is not wiped.
const liveRegions = document.querySelectorAll("[data-live]");
if (liveRegions.length > 0) {
  const refresh = async () => {
    if (document.hidden) return;
    let response;
    try {
      response = await fetch(location.href, { headers: { "X-Live": "1" } });
    } catch {
      return;
    }
    if (!response.ok || response.redirected) return;
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    for (const region of liveRegions) {
      const focused = document.activeElement;
      if (focused instanceof HTMLElement && region.contains(focused) && focused !== document.body) {
        continue;
      }
      if (region.querySelector("a:hover, button:hover, input:hover, select:hover, textarea:hover")) {
        continue;
      }
      const next = doc.querySelector(`[data-live="${region.dataset.live}"]`);
      if (next && region.innerHTML !== next.innerHTML) region.innerHTML = next.innerHTML;
    }
  };
  setInterval(refresh, 1000);
}
