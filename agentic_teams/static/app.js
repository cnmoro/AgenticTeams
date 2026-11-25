"use strict";
// AgenticTeams — interface do usuário humano (JS puro, sem build).

// A interface usa o cookie de sessão do login (/login); agentes e CLI usam Bearer token.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = { settings: {}, me: null, rooms: [], agents: [], room: null, messages: [], tasks: [], spawns: [],
            replyTo: null, newCount: {} };

const STATUS = { aberta: "aberta", em_andamento: "em andamento", feita: "feita", cancelada: "cancelada" };
const KIND_LABEL = { geral: "geral", projeto: "projeto", tema: "tema", legado: "legado" };

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { location.href = "/login" + location.hash; throw new Error("sessão expirada"); }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(text, ms = 3500) {
  const t = $("#toast"); t.textContent = text; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}
function fail(e) { console.error(e); toast("Erro: " + e.message, 6000); }

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "html") e.innerHTML = v;
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}
function fmtDay(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "long", year: "numeric" });
}
function ago(iso) {
  if (!iso) return Infinity;
  return (Date.now() - new Date(iso).getTime()) / 1000;
}
function bodyHtml(text) {
  const me = S.settings.human_name;
  return esc(text).replace(/(^|[^\w@/.])@([a-z0-9][a-z0-9_-]{1,39})/gi, (m, pre, name) => {
    const n = name.toLowerCase();
    const known = n === "todos" || n === "sala" || S.agents.some(a => a.name === n);
    if (!known) return m;
    return `${pre}<span class="mention${n === me ? " me" : ""}">@${esc(name)}</span>`;
  });
}
function seenKey(room) { return "at.seen." + room; }
function getSeen(room) { try { return +localStorage.getItem(seenKey(room)) || 0; } catch { return 0; } }
function setSeen(room, id) { try { localStorage.setItem(seenKey(room), id); } catch {} }

// ------------------------------------------------------------------ carregamento
async function loadAll() {
  [S.settings, S.me] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/me")]);
  await Promise.all([loadAgents(), loadRooms(), loadSpawns(), loadTaskCount()]);
  const fromHash = decodeURIComponent(location.hash.slice(1));
  const target = S.rooms.find(r => r.name === fromHash) ? fromHash : "geral";
  await selectRoom(target);
}
async function loadAgents() { S.agents = await api("GET", "/api/agents"); renderAgents(); }
async function loadRooms() { S.rooms = await api("GET", "/api/rooms"); renderRooms(); }
async function loadSpawns() { S.spawns = await api("GET", "/api/spawns"); renderSpawns(); }
async function loadTaskCount() {
  const t = await api("GET", "/api/tasks?status=abertas");
  $("#tasks-count").textContent = t.length || "";
}

// ------------------------------------------------------------------ salas
function renderRooms() {
  const ul = $("#rooms"); ul.replaceChildren();
  for (const r of S.rooms) {
    const hasNew = r.last_message_id > getSeen(r.name) && r.name !== S.room?.name;
    const n = S.newCount[r.name];
    ul.append(el("li", { class: (S.room?.name === r.name ? "active" : ""), onclick: () => selectRoom(r.name),
                         title: r.description || r.title },
      el("span", { class: "name", style: hasNew ? "font-weight:700" : "" }, "#" + r.name),
      r.unread ? el("span", { class: "badge", title: "menções a você" }, "@" + r.unread) : null,
      n ? el("span", { class: "badge muted", title: "mensagens novas" }, n) : null,
      el("span", { class: "kind" }, r.kind !== "projeto" ? KIND_LABEL[r.kind] : "")));
  }
}

async function selectRoom(name) {
  try {
    const [room, msgs] = await Promise.all([api("GET", `/api/rooms/${name}`),
                                            api("GET", `/api/rooms/${name}/messages?limit=300&mark_read=1`)]);
    S.room = room; S.messages = msgs; S.newCount[name] = 0;
    if (msgs.length) setSeen(name, msgs[msgs.length - 1].id);
    history.replaceState(null, "", "#" + name);
    const r = S.rooms.find(x => x.name === name); if (r) r.unread = 0;
    renderRoomHead(); renderMessages(true); renderRooms(); clearReply(); clearTaskSource();
    await loadRoomTasks();
  } catch (e) { fail(e); }
}

function renderRoomHead() {
  const r = S.room;
  $("#room-title").textContent = "#" + r.name + (r.title && r.title !== r.name ? " — " + r.title : "");
  $("#room-desc").textContent = r.description || "";
  const m = $("#room-members"); m.replaceChildren();
  for (const x of r.members) {
    m.append(el("span", { class: "chip" + (x.notify === "all" ? " all" : ""),
                          title: x.notify === "all" ? "recebe todas as mensagens" : (x.notify === "none" ? "só menção direta" : "recebe menções") },
                 x.name));
  }
  const legacy = r.kind === "legado";
  $("#composer").classList.toggle("disabled", legacy);
  $("#input").placeholder = legacy ? "Sala legado: somente leitura (importada do agents.txt)."
    : `Mensagem em #${r.name} como humano — @ menciona um agente, @todos acorda a sala. Enter envia, Shift+Enter quebra linha.`;
  $("#btn-add-member").classList.toggle("hidden", legacy);
}

// ------------------------------------------------------------------ mensagens
function msgEl(m) {
  const classes = ["msg"];
  if (m.author_kind === "human") classes.push("human");
  if (m.kind === "system" || m.author_kind === "system") classes.push("system");
  if (m.kind === "task") classes.push("tk");
  const tags = [];
  if (m.author_kind === "human") tags.push(el("span", { class: "tag human", title: "mensagem do usuário humano" }, "humano"));
  if (m.meta?.cross) tags.push(el("span", { class: "tag cross", title: "enviada por agente de fora da sala" }, "de fora"));
  if (m.kind === "legacy") tags.push(el("span", { class: "tag legacy" }, "agents.txt"));
  const ag = S.agents.find(a => a.name === m.author);
  const node = el("div", { class: classes.join(" "), id: "m" + m.id, "data-id": m.id },
    m.reply_to ? el("div", { class: "reply", onclick: () => flash(m.reply_to) }, "↪ em resposta a #" + m.reply_to) : null,
    el("div", { class: "meta" },
      el("span", { class: "author", title: ag?.handle || m.author }, m.author), ...tags,
      el("span", { class: "time", title: m.created_at }, fmtTime(m.created_at)),
      el("span", { class: "id" }, "#" + m.id)),
    el("div", { class: "body", html: bodyHtml(m.body) }));
  if (m.kind !== "legacy") {
    node.append(el("div", { class: "actions" },
      el("button", { onclick: () => setReply(m), title: "Responder a esta mensagem" }, "Responder"),
      el("button", { onclick: () => taskFromMessage(m), title: "Criar tarefa a partir desta mensagem" }, "Tarefa")));
  }
  return node;
}

function renderMessages(scroll) {
  const box = $("#messages"); box.replaceChildren();
  if (!S.messages.length) { box.append(el("div", { class: "empty" }, "Nenhuma mensagem nesta sala ainda.")); return; }
  let day = "";
  for (const m of S.messages) {
    const d = (m.created_at || "").slice(0, 10);
    if (d !== day) { day = d; box.append(el("div", { class: "day" }, fmtDay(m.created_at))); }
    box.append(msgEl(m));
  }
  if (scroll) box.scrollTop = box.scrollHeight;
}

function appendMessage(m) {
  const box = $("#messages");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  if (!S.messages.length) box.replaceChildren();
  const last = S.messages[S.messages.length - 1];
  if (!last || (last.created_at || "").slice(0, 10) !== (m.created_at || "").slice(0, 10))
    box.append(el("div", { class: "day" }, fmtDay(m.created_at)));
  S.messages.push(m);
  box.append(msgEl(m));
  setSeen(m.room, m.id);
  if (atBottom || m.author_kind === "human") box.scrollTop = box.scrollHeight;
}

function flash(id) {
  const n = document.getElementById("m" + id);
  if (!n) { toast("Mensagem #" + id + " não está carregada nesta sala."); return; }
  n.scrollIntoView({ block: "center", behavior: "smooth" });
  n.classList.remove("flash"); void n.offsetWidth; n.classList.add("flash");
}

function setReply(m) {
  S.replyTo = m.id;
  const bar = $("#reply-bar"); bar.classList.remove("hidden");
  bar.querySelector("span").textContent = `Respondendo a #${m.id} de ${m.author}: ${m.body.slice(0, 80)}`;
  if (m.author_kind === "agent" && !$("#input").value.includes("@" + m.author)) $("#input").value = "@" + m.author + " " + $("#input").value;
  $("#input").focus();
}
function clearReply() { S.replyTo = null; $("#reply-bar").classList.add("hidden"); }

async function send(e) {
  e?.preventDefault();
  const input = $("#input");
  const body = input.value.trim();
  if (!body || !S.room) return;
  try {
    await api("POST", `/api/rooms/${S.room.name}/messages`, { body, reply_to: S.replyTo });
    input.value = ""; clearReply(); closeMenu();
  } catch (err) { fail(err); }
}

// ------------------------------------------------------------------ autocomplete de @
const menu = { open: false, items: [], sel: 0, start: 0 };
function mentionCandidates(prefix) {
  const p = prefix.toLowerCase();
  const inRoom = new Set((S.room?.members || []).map(m => m.name));
  const list = [{ name: "todos", info: "acorda todos os membros da sala" },
                ...S.agents.filter(a => a.kind === "agent").map(a => ({ name: a.name, info: a.folder || "", room: inRoom.has(a.name), online: ago(a.last_seen) < 120 }))];
  return list.filter(x => x.name.startsWith(p) || (p.length > 1 && x.name.includes(p)))
             .sort((a, b) => (b.room ? 1 : 0) - (a.room ? 1 : 0)).slice(0, 10);
}
function updateMenu() {
  const input = $("#input");
  const upto = input.value.slice(0, input.selectionStart);
  const m = upto.match(/(?:^|[\s(])@([a-z0-9_-]*)$/i);
  if (!m) return closeMenu();
  menu.start = input.selectionStart - m[1].length;
  menu.items = mentionCandidates(m[1]);
  if (!menu.items.length) return closeMenu();
  menu.sel = Math.min(menu.sel, menu.items.length - 1);
  const ul = $("#mention-menu"); ul.replaceChildren();
  menu.items.forEach((it, i) => ul.append(el("li", { class: i === menu.sel ? "sel" : "", role: "option",
      onmousedown: ev => { ev.preventDefault(); pickMention(i); } },
    it.name === "todos" ? "" : el("span", { class: "dot" + (it.online ? " on" : "") }),
    el("strong", {}, "@" + it.name),
    it.room === false ? el("span", { class: "tag cross", title: "não é membro desta sala; recebe mesmo assim" }, "fora") : null,
    el("small", {}, it.info))));
  ul.classList.remove("hidden"); menu.open = true;
}
function closeMenu() { $("#mention-menu").classList.add("hidden"); menu.open = false; menu.sel = 0; }
function pickMention(i) {
  const input = $("#input"); const it = menu.items[i]; if (!it) return;
  const before = input.value.slice(0, menu.start), after = input.value.slice(input.selectionStart);
  input.value = before + it.name + " " + after;
  const pos = (before + it.name + " ").length;
  input.setSelectionRange(pos, pos); closeMenu(); input.focus();
}

// ------------------------------------------------------------------ agentes
function renderAgents() {
  const ul = $("#agents"); ul.replaceChildren();
  const people = S.agents.filter(a => a.kind === "agent" || a.kind === "human");
  for (const a of people) {
    const s = ago(a.last_seen);
    const dot = a.kind === "human" ? "human" : (s < 120 ? "on" : s < 1800 ? "idle" : "");
    const status = a.kind === "human" ? "você (humano)" : (s < 120 ? "online (vigia ativo)" : a.last_seen ? "visto " + new Date(a.last_seen).toLocaleString("pt-BR") : "nunca visto");
    ul.append(el("li", { title: `${a.handle}\n${status}\nsalas: ${a.rooms.join(", ")}` },
      el("span", { class: "dot " + dot }),
      el("span", { class: "name" }, a.name, a.folder ? el("span", { class: "folder" }, a.folder) : null)));
  }
  if (!people.some(a => a.kind === "agent")) ul.append(el("li", { class: "muted small" }, "nenhum agente registrado"));
  const legacy = S.agents.filter(a => a.kind === "legacy").length;
  if (legacy) ul.append(el("li", { class: "muted small", title: "autores importados do agents.txt" }, `+ ${legacy} do agents.txt`));
}

// ------------------------------------------------------------------ tarefas
async function loadRoomTasks() {
  if (!S.room) return;
  const showDone = $("#task-show-done").checked;
  S.tasks = await api("GET", `/api/tasks?room=${S.room.name}${showDone ? "" : "&status=abertas"}`);
  renderTaskList($("#tasks"), S.tasks, false);
  const sel = $("#task-assignee"); const cur = sel.value; sel.replaceChildren(el("option", { value: "" }, "sem responsável"));
  for (const a of S.agents.filter(a => a.kind === "agent" || a.kind === "human"))
    sel.append(el("option", { value: a.name }, a.name + (S.room.members.some(m => m.name === a.name) ? "" : " (fora da sala)")));
  sel.value = cur;
}

function renderTaskList(ul, tasks, showRoom) {
  ul.replaceChildren();
  if (!tasks.length) { ul.append(el("li", { class: "muted small" }, "Nenhuma tarefa.")); return; }
  for (const t of tasks) {
    const statusSel = el("select", { onchange: e => patchTask(t.id, { status: e.target.value }) },
      ...Object.entries(STATUS).map(([v, l]) => el("option", { value: v, selected: v === t.status ? "" : null }, l)));
    const whoSel = el("select", { onchange: e => patchTask(t.id, { assignee: e.target.value }) },
      el("option", { value: "" }, "sem responsável"),
      ...S.agents.filter(a => a.kind === "agent" || a.kind === "human")
        .map(a => el("option", { value: a.name, selected: a.name === t.assignee ? "" : null }, a.name)));
    ul.append(el("li", { class: "task " + t.status },
      el("div", { class: "title" }, `#${t.id} ${t.title}`),
      t.body ? el("div", { class: "small muted" }, t.body) : null,
      el("div", { class: "info" },
        el("span", { class: "st " + t.status }, STATUS[t.status]),
        showRoom ? el("a", { onclick: () => { $("#dlg-tasks").close(); selectRoom(t.room); } }, "#" + t.room) : null,
        el("span", {}, "por " + t.creator),
        t.source_message_id ? el("a", { onclick: () => gotoMessage(t.room, t.source_message_id) }, "origem #" + t.source_message_id) : null),
      el("div", { class: "controls" }, statusSel, whoSel)));
  }
}

async function patchTask(id, body) {
  try { await api("PATCH", `/api/tasks/${id}`, body); } catch (e) { fail(e); }
}

function taskFromMessage(m) {
  document.querySelector(".app").classList.remove("no-panel");
  $("#task-source").value = m.id;
  $("#task-source-label").textContent = `vinculada à mensagem #${m.id} de ${m.author}`;
  $("#task-title").value = m.body.replace(/\s+/g, " ").slice(0, 100);
  if (m.author_kind === "agent") $("#task-assignee").value = m.author;
  $("#task-title").focus(); $("#task-title").select();
}
function clearTaskSource() { $("#task-source").value = ""; $("#task-source-label").textContent = ""; }

async function createTask(e) {
  e.preventDefault();
  try {
    await api("POST", "/api/tasks", { room: S.room.name, title: $("#task-title").value,
      assignee: $("#task-assignee").value || null, source_message_id: +$("#task-source").value || null });
    $("#task-title").value = ""; clearTaskSource();
  } catch (err) { fail(err); }
}

async function gotoMessage(room, id) {
  if (S.room?.name !== room) await selectRoom(room);
  flash(id);
}

async function openAllTasks() {
  const f = $("#all-tasks-filter").value;
  const tasks = await api("GET", "/api/tasks" + (f ? "?status=" + f : ""));
  renderTaskList($("#all-tasks"), tasks, true);
  if (!$("#dlg-tasks").open) $("#dlg-tasks").showModal();
}

// ------------------------------------------------------------------ subagentes
function renderSpawns() {
  const pending = S.spawns.filter(s => s.status === "pendente").length;
  const b = $("#spawns-count"); b.textContent = pending; b.classList.toggle("hidden", !pending);
  const ul = $("#spawns"); ul.replaceChildren();
  if (!S.spawns.length) ul.append(el("li", { class: "muted" }, "Nenhum subagente ainda."));
  for (const s of S.spawns) {
    ul.append(el("li", { class: s.status },
      el("div", {}, el("strong", {}, `#${s.id} ${s.agent_name}`), ` em `, el("code", {}, s.folder),
         ` — ${s.status} · pedido por ${s.requested_by} · modo ${s.permission_mode} · ${new Date(s.created_at).toLocaleString("pt-BR")}`),
      el("div", { class: "prompt" }, s.prompt),
      s.output ? el("pre", { class: "result" }, s.output) : null,
      s.status === "pendente" ? el("div", { class: "controls" },
        el("button", { class: "primary", onclick: () => decideSpawn(s.id, "approve") }, "Aprovar e iniciar"),
        el("button", { onclick: () => decideSpawn(s.id, "reject") }, "Recusar")) : null));
  }
}
async function decideSpawn(id, action) {
  try { const s = await api("POST", `/api/spawns/${id}/${action}`); toast(`Subagente #${id}: ${s.status}`); await loadSpawns(); }
  catch (e) { fail(e); }
}
function openSpawns() {
  const roots = S.settings.spawn_roots || [];
  $("#spawn-roots").textContent = roots.length ? roots.join(", ") : "nenhuma (invocação desativada; configure spawn_roots)";
  const mode = $("#spawn-mode"); mode.replaceChildren(...S.settings.spawn_permission_modes.map(m => el("option", { value: m }, m)));
  const form = $("#spawn-form");
  if (!form.rooms.value && S.room && S.room.kind !== "legado") form.rooms.value = S.room.name;
  $("#dlg-spawns").showModal();
}
async function submitSpawn(e) {
  e.preventDefault();
  const f = e.target;
  const out = $("#spawn-result");
  try {
    const s = await api("POST", "/api/spawns", { folder: f.folder.value.trim(), name: f.name.value.trim(), prompt: f.prompt.value,
      rooms: f.rooms.value.split(",").map(x => x.trim()).filter(Boolean), permission_mode: f.mode.value });
    out.textContent = `#${s.id} ${s.status}\n${s.output || ""}`; out.classList.remove("hidden");
    if (s.status === "iniciado") { f.name.value = ""; f.prompt.value = ""; }
    await Promise.all([loadSpawns(), loadAgents()]);
  } catch (err) { out.textContent = "Erro: " + err.message; out.classList.remove("hidden"); }
}

// ------------------------------------------------------------------ busca
async function doSearch(text) {
  const res = await api("GET", "/api/search?q=" + encodeURIComponent(text));
  $("#search-title").textContent = `Busca: “${text}” (${res.length})`;
  const box = $("#search-results"); box.replaceChildren();
  if (!res.length) box.append(el("div", { class: "empty" }, "Nada encontrado."));
  for (const m of res) {
    const n = msgEl(m); n.querySelector(".actions")?.remove();
    n.querySelector(".meta").prepend(el("span", { class: "chip" }, "#" + m.room));
    n.style.cursor = "pointer";
    n.addEventListener("click", () => { $("#dlg-search").close(); gotoMessage(m.room, m.id); });
    box.append(n);
  }
  if (!$("#dlg-search").open) $("#dlg-search").showModal();
}

// ------------------------------------------------------------------ diálogos de sala
function openNewRoom() {
  const box = $("#room-form-members"); box.replaceChildren();
  for (const a of S.agents.filter(a => a.kind === "agent"))
    box.append(el("label", { class: "check" }, el("input", { type: "checkbox", value: a.name }), a.name));
  if (!box.children.length) box.append(el("span", { class: "muted small" }, "Nenhum agente registrado ainda."));
  $("#room-form").reset(); $("#dlg-room").showModal();
}
async function submitRoom(e) {
  if (e.submitter?.value !== "ok") return;
  e.preventDefault();
  const f = e.target;
  try {
    const r = await api("POST", "/api/rooms", { name: f.name.value.trim(), title: f.title.value.trim(), description: f.description.value.trim(),
      kind: f.kind.value, members: $$("#room-form-members input:checked").map(i => i.value) });
    $("#dlg-room").close(); await loadRooms(); await selectRoom(r.name);
  } catch (err) { fail(err); }
}
function openAddMember() {
  const inRoom = new Set(S.room.members.map(m => m.name));
  const sel = $("#member-agent"); sel.replaceChildren();
  for (const a of S.agents.filter(a => a.kind === "agent" && !inRoom.has(a.name))) sel.append(el("option", { value: a.name }, a.name));
  if (!sel.children.length) { toast("Todos os agentes já estão nesta sala."); return; }
  $("#dlg-member").showModal();
}
async function submitMember(e) {
  if (e.submitter?.value !== "ok") return;
  e.preventDefault();
  const f = e.target;
  try {
    S.room = await api("POST", `/api/rooms/${S.room.name}/join`, { agent: f.agent.value, notify: f.notify.value });
    $("#dlg-member").close(); renderRoomHead(); loadRoomTasks();
  } catch (err) { fail(err); }
}

// ------------------------------------------------------------------ tempo real (SSE)
function connect() {
  const es = new EventSource("/api/stream");
  const conn = $("#conn");
  es.addEventListener("hello", () => { conn.textContent = "conectado"; conn.className = "conn on"; });
  es.onerror = () => { conn.textContent = "reconectando…"; conn.className = "conn off"; };
  es.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    const r = S.rooms.find(x => x.name === m.room);
    if (r) r.last_message_id = m.id;
    if (S.room?.name === m.room) {
      appendMessage(m);
      if (m.mentions.includes(S.settings.human_name)) api("POST", "/api/inbox/read", { room: m.room }).catch(() => {});
    } else {
      S.newCount[m.room] = (S.newCount[m.room] || 0) + 1;
      if (r && m.mentions.includes(S.settings.human_name)) { r.unread = (r.unread || 0) + 1; toast(`${m.author} mencionou você em #${m.room}`); }
      renderRooms();
    }
  });
  es.addEventListener("task", () => { loadRoomTasks().catch(fail); loadTaskCount(); if ($("#dlg-tasks").open) openAllTasks(); });
  es.addEventListener("room", ev => {
    const d = JSON.parse(ev.data);
    loadRooms().catch(fail);
    if (S.room?.name === d.name) { S.room = { ...S.room, ...d }; renderRoomHead(); }
  });
  es.addEventListener("agent", () => loadAgents().catch(fail));
  es.addEventListener("spawn", () => loadSpawns().catch(fail));
}

// ------------------------------------------------------------------ eventos da página
function bind() {
  $("#composer").addEventListener("submit", send);
  $("#btn-logout").addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST" }).catch(() => {});
    location.href = "/login";
  });
  const input = $("#input");
  input.addEventListener("input", updateMenu);
  input.addEventListener("click", updateMenu);
  input.addEventListener("keydown", e => {
    if (menu.open) {
      if (e.key === "ArrowDown") { e.preventDefault(); menu.sel = (menu.sel + 1) % menu.items.length; updateMenu(); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); menu.sel = (menu.sel - 1 + menu.items.length) % menu.items.length; updateMenu(); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pickMention(menu.sel); return; }
      if (e.key === "Escape") { closeMenu(); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    if (e.key === "Escape") clearReply();
  });
  input.addEventListener("blur", () => setTimeout(closeMenu, 150));
  $("#reply-cancel").addEventListener("click", clearReply);
  $("#task-form").addEventListener("submit", createTask);
  $("#task-show-done").addEventListener("change", () => loadRoomTasks().catch(fail));
  $("#btn-toggle-panel").addEventListener("click", () => document.querySelector(".app").classList.toggle("no-panel"));
  $("#btn-new-room").addEventListener("click", openNewRoom);
  $("#room-form").addEventListener("submit", submitRoom);
  $("#btn-add-member").addEventListener("click", openAddMember);
  $("#member-form").addEventListener("submit", submitMember);
  $("#btn-tasks").addEventListener("click", () => openAllTasks().catch(fail));
  $("#all-tasks-filter").addEventListener("change", () => openAllTasks().catch(fail));
  $("#btn-spawns").addEventListener("click", openSpawns);
  $("#spawn-form").addEventListener("submit", submitSpawn);
  $("#search").addEventListener("keydown", e => { if (e.key === "Enter" && e.target.value.trim()) doSearch(e.target.value.trim()).catch(fail); });
  $$("[data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));
  window.addEventListener("hashchange", () => { const n = decodeURIComponent(location.hash.slice(1)); if (n && n !== S.room?.name) selectRoom(n); });
  setInterval(() => loadAgents().catch(() => {}), 30000);
}

bind();
loadAll().then(connect).catch(fail);
