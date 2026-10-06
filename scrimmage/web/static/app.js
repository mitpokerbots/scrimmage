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
