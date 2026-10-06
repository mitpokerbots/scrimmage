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
