"use strict";
const form = document.getElementById("login");
const error = document.getElementById("error");

// The public health endpoint carries the server's UI language; ?lang and localStorage still win.
applyI18n();
fetch("/api/health").then(r => r.json()).then(h => { setLang(h.language); applyI18n(); }).catch(() => {});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  error.hidden = true;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    const r = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: form.email.value, password: form.password.value }),
    });
    if (r.ok) { location.href = "/" + location.search + location.hash; return; }
    const data = await r.json().catch(() => ({}));
    error.textContent = data.error || t("login.failed");
  } catch {
    error.textContent = t("login.unavailable");
  } finally {
    button.disabled = false;
  }
  error.hidden = false;
  form.password.select();
});
