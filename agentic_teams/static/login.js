"use strict";
const form = document.getElementById("login");
const error = document.getElementById("error");
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
    if (r.ok) { location.href = "/" + location.hash; return; }
    const data = await r.json().catch(() => ({}));
    error.textContent = data.error || "Não foi possível entrar.";
  } catch {
    error.textContent = "Servidor indisponível.";
  } finally {
    button.disabled = false;
  }
  error.hidden = false;
  form.password.select();
});
