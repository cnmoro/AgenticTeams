"use strict";
// AgenticTeams — web UI for the human user (plain JS, no build step).
// Strings come from i18n.js via t(); English is the default.

// The UI uses the session cookie from /login; agents and the CLI use a Bearer token.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = { settings: {}, me: null, rooms: [], agents: [], room: null, messages: [], tasks: [], spawns: [],
            replyTo: null, newCount: {} };

const STATUSES = ["open", "in_progress", "done", "cancelled"];
const statusLabel = st => STATUSES.includes(st) ? t("status." + st) : st;
const kindLabel = k => ["general", "project", "topic", "legacy"].includes(k) ? t("kind." + k) : k;
const spawnStatusLabel = st => ["pending", "approved", "started", "error", "rejected"].includes(st) ? t("spawn_status." + st) : st;
// Room-wide mention keyword offered in the @ menu (the server accepts both "all" and "todos").
const allKeyword = () => currentLang() === "pt" ? "todos" : "all";

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { location.href = "/login" + location.search + location.hash; throw new Error(t("session_expired")); }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(text, ms = 3500) {
  const box = $("#toast"); box.textContent = text; box.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => box.classList.add("hidden"), ms);
}
function fail(e) { console.error(e); toast(t("error", { msg: e.message }), 6000); }

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
  return d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
}
function fmtDay(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString(locale(), { weekday: "long", day: "2-digit", month: "long", year: "numeric" });
}
function ago(iso) {
  if (!iso) return Infinity;
  return (Date.now() - new Date(iso).getTime()) / 1000;
}
function mentionsHtml(escaped) {
  const me = S.settings.human_name;
  return escaped.replace(/(^|[^\w@/.])@([a-z0-9][a-z0-9_-]{1,39})/gi, (m, pre, name) => {
    const n = name.toLowerCase();
    const known = n === "all" || n === "todos" || n === "sala" || S.agents.some(a => a.name === n);
    if (!known) return m;
    return `${pre}<span class="mention${n === me ? " me" : ""}">@${esc(name)}</span>`;
  });
}
// Message body: Markdown (markdown.js escapes all HTML first) plus @mention highlighting.
function bodyHtml(text) { return renderMarkdown(text, mentionsHtml); }
function seenKey(room) { return "at.seen." + room; }
function getSeen(room) { try { return +localStorage.getItem(seenKey(room)) || 0; } catch { return 0; } }
function setSeen(room, id) { try { localStorage.setItem(seenKey(room), id); } catch {} }

// ------------------------------------------------------------------ loading
async function loadAll() {
  [S.settings, S.me] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/me")]);
  setLang(S.settings.language); applyI18n();
  $("#who").textContent = S.me.user || S.me.name; $("#who").title = S.me.user || S.me.handle;
  await Promise.all([loadAgents(), loadRooms(), loadSpawns(), loadTaskCount()]);
  const fromHash = decodeURIComponent(location.hash.slice(1));
  const target = S.rooms.find(r => r.name === fromHash) ? fromHash
    : (S.rooms.find(r => r.kind === "general") || S.rooms[0])?.name;
  if (!target) return;
  await selectRoom(target);
}
async function loadAgents() { S.agents = await api("GET", "/api/agents"); renderAgents(); }
async function loadRooms() { S.rooms = await api("GET", "/api/rooms"); renderRooms(); }
async function loadSpawns() { S.spawns = await api("GET", "/api/spawns"); renderSpawns(); }
async function loadTaskCount() {
  const open = await api("GET", "/api/tasks?status=open_any");
  $("#tasks-count").textContent = open.length || "";
}

// ------------------------------------------------------------------ rooms
const roomHasNew = r => r.last_message_id > getSeen(r.name) && r.name !== S.room?.name;
function renderRooms() {
  const ul = $("#rooms"); ul.replaceChildren();
  const f = filterText();
  const shown = S.rooms.filter(r => !f || r.name.includes(f) || (r.title || "").toLowerCase().includes(f));
  for (const r of shown) {
    const hasNew = roomHasNew(r);
    const n = S.newCount[r.name];
    ul.append(el("li", { class: [S.room?.name === r.name ? "active" : "", hasNew ? "unseen" : ""].join(" ").trim(),
                         onclick: () => selectRoom(r.name), title: r.description || r.title },
      el("span", { class: "dot " + (hasNew ? "activity" : "on") }),
      el("span", { class: "name" }, "#" + r.name),
      r.unread ? el("span", { class: "badge", title: t("rooms.mentions") }, "@" + r.unread) : null,
      n ? el("span", { class: "badge muted", title: t("rooms.new_messages") }, n) : null,
      el("span", { class: "kind" }, r.kind !== "project" ? kindLabel(r.kind) : "")));
  }
  $("#rooms-count").textContent = S.rooms.length || "";
  renderStrip();
}

// ------------------------------------------------------------------ strip of open rooms (like Agent Warehouse tabs)
function openRooms() { try { return JSON.parse(localStorage.getItem("at.open") || "[]"); } catch { return []; } }
function saveOpenRooms(list) { try { localStorage.setItem("at.open", JSON.stringify(list)); } catch {} }
function renderStrip() {
  const strip = $("#strip"); strip.replaceChildren();
  const names = openRooms().filter(n => S.rooms.some(r => r.name === n));
  for (const name of names) {
    const r = S.rooms.find(x => x.name === name);
    const active = S.room?.name === name;
    strip.append(el("div", { class: "chip" + (active ? " active" : "") + (roomHasNew(r) ? " activity" : ""), role: "tab",
                             "aria-selected": active ? "true" : "false", title: r.title || r.name, onclick: () => selectRoom(name) },
      el("span", { class: "dot" }), el("span", { class: "name" }, "#" + name),
      el("button", { class: "chip-close", type: "button", "aria-label": t("strip.close"), title: t("strip.close"),
                     onclick: ev => { ev.stopPropagation(); closeRoomTab(name); } }, "✕")));
  }
}
function closeRoomTab(name) {
  const list = openRooms().filter(n => n !== name);
  saveOpenRooms(list);
  if (S.room?.name === name) {
    const next = list[list.length - 1] || (S.rooms.find(r => r.kind === "general") || S.rooms[0])?.name;
    if (next) { selectRoom(next); return; }
  }
  renderStrip();
}
function filterText() { return ($("#find").hidden ? "" : $("#search").value.trim().toLowerCase()); }

async function selectRoom(name) {
  try {
    const [room, msgs] = await Promise.all([api("GET", `/api/rooms/${name}`),
                                            api("GET", `/api/rooms/${name}/messages?limit=300&mark_read=1`)]);
    S.room = room; S.messages = msgs; S.newCount[name] = 0;
    const open = openRooms(); if (!open.includes(name)) { open.push(name); saveOpenRooms(open.slice(-12)); }
    if (msgs.length) setSeen(name, msgs[msgs.length - 1].id);
    history.replaceState(null, "", "#" + name);
    const r = S.rooms.find(x => x.name === name); if (r) r.unread = 0;
    renderRoomHead(); renderMessages(true); renderRooms(); clearReply(); clearTaskSource();
    await loadRoomTasks();
  } catch (e) { fail(e); }
}

function renderRoomHead() {
  const r = S.room;
  $("#room-title").textContent = "#" + r.name + (r.title && r.title !== r.name ? " · " + r.title : "");
  $("#room-desc").textContent = r.description || "";
  const m = $("#room-members"); m.replaceChildren();
  for (const x of r.members) {
    m.append(el("span", { class: "member" + (x.notify === "all" ? " all" : ""),
                          title: x.notify === "all" ? t("member.all") : (x.notify === "none" ? t("member.none") : t("member.mentions")) },
                 x.name));
  }
  const legacy = r.kind === "legacy";
  $("#composer").classList.toggle("disabled", legacy);
  $("#input").placeholder = legacy ? t("composer.legacy")
    : t("composer.placeholder_room", { room: r.name, all: allKeyword() });
  $("#btn-add-member").classList.toggle("hidden", legacy);
}

// ------------------------------------------------------------------ messages
function msgEl(m) {
  const classes = ["msg"];
  if (m.author_kind === "human") classes.push("human");
  if (m.kind === "system" || m.author_kind === "system") classes.push("system");
  if (m.kind === "task") classes.push("tk");
  const tags = [];
  if (m.author_kind === "human") tags.push(el("span", { class: "tag human", title: t("msg.human_title") }, t("msg.human")));
  if (m.meta?.cross) tags.push(el("span", { class: "tag cross", title: t("msg.cross_title") }, t("msg.cross")));
  if (m.kind === "legacy") tags.push(el("span", { class: "tag legacy" }, "agents.txt"));
  const ag = S.agents.find(a => a.name === m.author);
  const node = el("div", { class: classes.join(" "), id: "m" + m.id, "data-id": m.id },
    m.reply_to ? el("div", { class: "reply", onclick: () => flash(m.reply_to) }, t("msg.in_reply_to", { id: m.reply_to })) : null,
    el("div", { class: "meta" },
      el("span", { class: "author", title: ag?.handle || m.author }, m.author), ...tags,
      el("span", { class: "time", title: m.created_at }, fmtTime(m.created_at)),
      el("span", { class: "id" }, "#" + m.id)),
    el("div", { class: "body", html: bodyHtml(m.body) }));
  if (m.kind !== "legacy") {
    node.append(el("div", { class: "actions" },
      el("button", { onclick: () => setReply(m), title: t("msg.reply_title") }, t("msg.reply")),
      el("button", { onclick: () => taskFromMessage(m), title: t("msg.task_title") }, t("msg.task"))));
  }
  return node;
}

function renderMessages(scroll) {
  const box = $("#messages"); box.replaceChildren();
  if (!S.messages.length) { box.append(el("div", { class: "empty" }, t("msg.empty"))); return; }
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
  if (!n) { toast(t("msg.not_loaded", { id })); return; }
  n.scrollIntoView({ block: "center", behavior: "smooth" });
  n.classList.remove("flash"); void n.offsetWidth; n.classList.add("flash");
}

function setReply(m) {
  S.replyTo = m.id;
  const bar = $("#reply-bar"); bar.classList.remove("hidden");
  bar.querySelector("span").textContent = t("reply.to", { id: m.id, author: m.author, text: m.body.slice(0, 80) });
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

// ------------------------------------------------------------------ @ autocomplete
const menu = { open: false, items: [], sel: 0, start: 0 };
function mentionCandidates(prefix) {
  const p = prefix.toLowerCase();
  const inRoom = new Set((S.room?.members || []).map(m => m.name));
  const list = [{ name: allKeyword(), info: t("mention.all"), everyone: true },
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
    it.everyone ? "" : el("span", { class: "dot" + (it.online ? " on" : "") }),
    el("strong", {}, "@" + it.name),
    it.room === false ? el("span", { class: "tag cross", title: t("mention.outside_title") }, t("mention.outside")) : null,
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

// ------------------------------------------------------------------ agents
function renderAgents() {
  const ul = $("#agents"); ul.replaceChildren();
  const f = filterText();
  const people = S.agents.filter(a => (a.kind === "agent" || a.kind === "human") &&
                                      (!f || a.name.includes(f) || (a.folder || "").toLowerCase().includes(f)));
  $("#agents-count").textContent = S.agents.filter(a => a.kind === "agent").length || "";
  for (const a of people) {
    const s = ago(a.last_seen);
    const dot = a.kind === "human" ? "human" : (s < 120 ? "on" : s < 1800 ? "idle" : "");
    const status = a.kind === "human" ? t("agents.you") : (s < 120 ? t("agents.online")
      : a.last_seen ? t("agents.seen", { when: new Date(a.last_seen).toLocaleString(locale()) }) : t("agents.never"));
    ul.append(el("li", { title: `${a.handle}\n${status}\n${t("agents.rooms", { rooms: a.rooms.join(", ") })}` },
      el("span", { class: "dot " + dot }),
      el("span", { class: "name" }, a.name, a.folder ? el("span", { class: "folder-path" }, a.folder) : null)));
  }
  if (!people.some(a => a.kind === "agent") && !f) ul.append(el("li", { class: "note" }, t("agents.none")));
  const legacy = S.agents.filter(a => a.kind === "legacy").length;
  if (legacy && !f) ul.append(el("li", { class: "note", title: t("agents.legacy_title") }, t("agents.legacy", { n: legacy })));
}

// ------------------------------------------------------------------ tasks
async function loadRoomTasks() {
  if (!S.room) return;
  const showDone = $("#task-show-done").checked;
  S.tasks = await api("GET", `/api/tasks?room=${S.room.name}${showDone ? "" : "&status=open_any"}`);
  renderTaskList($("#tasks"), S.tasks, false);
  const sel = $("#task-assignee"); const cur = sel.value; sel.replaceChildren(el("option", { value: "" }, t("tasks.no_assignee")));
  for (const a of S.agents.filter(a => a.kind === "agent" || a.kind === "human"))
    sel.append(el("option", { value: a.name }, a.name + (S.room.members.some(m => m.name === a.name) ? "" : t("tasks.outside_room"))));
  sel.value = cur;
}

function renderTaskList(ul, tasks, showRoom) {
  ul.replaceChildren();
  if (!tasks.length) { ul.append(el("li", { class: "muted small" }, t("tasks.none"))); return; }
  for (const task of tasks) {
    const statusSel = el("select", { onchange: e => patchTask(task.id, { status: e.target.value }) },
      ...STATUSES.map(v => el("option", { value: v, selected: v === task.status ? "" : null }, statusLabel(v))));
    const whoSel = el("select", { onchange: e => patchTask(task.id, { assignee: e.target.value }) },
      el("option", { value: "" }, t("tasks.no_assignee")),
      ...S.agents.filter(a => a.kind === "agent" || a.kind === "human")
        .map(a => el("option", { value: a.name, selected: a.name === task.assignee ? "" : null }, a.name)));
    ul.append(el("li", { class: "task " + task.status },
      el("div", { class: "title" }, `#${task.id} ${task.title}`),
      task.body ? el("div", { class: "small muted" }, task.body) : null,
      el("div", { class: "info" },
        el("span", { class: "st " + task.status }, statusLabel(task.status)),
        showRoom ? el("a", { onclick: () => { $("#dlg-tasks").close(); selectRoom(task.room); } }, "#" + task.room) : null,
        el("span", {}, t("tasks.by", { who: task.creator })),
        task.source_message_id ? el("a", { onclick: () => gotoMessage(task.room, task.source_message_id) }, t("tasks.source", { id: task.source_message_id })) : null),
      el("div", { class: "controls" }, statusSel, whoSel)));
  }
}

async function patchTask(id, body) {
  try { await api("PATCH", `/api/tasks/${id}`, body); } catch (e) { fail(e); }
}

function taskFromMessage(m) {
  document.querySelector(".app").classList.remove("no-panel");
  $("#task-source").value = m.id;
  $("#task-source-label").textContent = t("tasks.linked", { id: m.id, author: m.author });
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

// ------------------------------------------------------------------ subagents
function renderSpawns() {
  const pending = S.spawns.filter(s => s.status === "pending").length;
  const b = $("#spawns-count"); b.textContent = pending; b.classList.toggle("hidden", !pending);
  const ul = $("#spawns"); ul.replaceChildren();
  if (!S.spawns.length) ul.append(el("li", { class: "muted" }, t("spawn.none")));
  for (const s of S.spawns) {
    ul.append(el("li", { class: s.status },
      el("div", {}, el("strong", {}, `#${s.id} ${s.agent_name}`), t("spawn.in"), el("code", {}, s.folder),
         t("spawn.line", { status: spawnStatusLabel(s.status), who: s.requested_by, mode: s.permission_mode,
                           when: new Date(s.created_at).toLocaleString(locale()) })),
      el("div", { class: "prompt" }, s.prompt),
      s.output ? el("pre", { class: "result" }, s.output) : null,
      s.status === "pending" ? el("div", { class: "controls" },
        el("button", { class: "primary", onclick: () => decideSpawn(s.id, "approve") }, t("spawn.approve")),
        el("button", { onclick: () => decideSpawn(s.id, "reject") }, t("spawn.reject"))) : null));
  }
}
async function decideSpawn(id, action) {
  try { const s = await api("POST", `/api/spawns/${id}/${action}`); toast(t("spawn.result", { id, status: spawnStatusLabel(s.status) })); await loadSpawns(); }
  catch (e) { fail(e); }
}
function openSpawns() {
  const roots = S.settings.spawn_roots || [];
  $("#spawn-roots").textContent = roots.length ? roots.join(", ") : t("spawn.no_roots");
  const mode = $("#spawn-mode"); mode.replaceChildren(...S.settings.spawn_permission_modes.map(m => el("option", { value: m }, m)));
  const form = $("#spawn-form");
  if (!form.rooms.value && S.room && S.room.kind !== "legacy") form.rooms.value = S.room.name;
  $("#dlg-spawns").showModal();
}
async function submitSpawn(e) {
  e.preventDefault();
  const f = e.target;
  const out = $("#spawn-result");
  try {
    const s = await api("POST", "/api/spawns", { folder: f.folder.value.trim(), name: f.name.value.trim(), prompt: f.prompt.value,
      rooms: f.rooms.value.split(",").map(x => x.trim()).filter(Boolean), permission_mode: f.mode.value });
    out.textContent = `#${s.id} ${spawnStatusLabel(s.status)}\n${s.output || ""}`; out.classList.remove("hidden");
    if (s.status === "started") { f.name.value = ""; f.prompt.value = ""; }
    await Promise.all([loadSpawns(), loadAgents()]);
  } catch (err) { out.textContent = t("error", { msg: err.message }); out.classList.remove("hidden"); }
}

// ------------------------------------------------------------------ search
async function doSearch(text) {
  const res = await api("GET", "/api/search?q=" + encodeURIComponent(text));
  $("#search-title").textContent = t("search.results", { q: text, n: res.length });
  const box = $("#search-results"); box.replaceChildren();
  if (!res.length) box.append(el("div", { class: "empty" }, t("search.none")));
  for (const m of res) {
    const n = msgEl(m); n.querySelector(".actions")?.remove();
    n.querySelector(".meta").prepend(el("span", { class: "member" }, "#" + m.room));
    n.style.cursor = "pointer";
    n.addEventListener("click", () => { $("#dlg-search").close(); gotoMessage(m.room, m.id); });
    box.append(n);
  }
  if (!$("#dlg-search").open) $("#dlg-search").showModal();
}

// ------------------------------------------------------------------ room dialogs
function openNewRoom() {
  const box = $("#room-form-members"); box.replaceChildren();
  for (const a of S.agents.filter(a => a.kind === "agent"))
    box.append(el("label", { class: "check" }, el("input", { type: "checkbox", value: a.name }), a.name));
  if (!box.children.length) box.append(el("span", { class: "muted small" }, t("room.no_agents")));
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
  if (!sel.children.length) { toast(t("member.all_in")); return; }
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

// ------------------------------------------------------------------ real time (SSE)
function connect() {
  const es = new EventSource("/api/stream");
  const setConn = up => $$(".conn").forEach(c => {
    c.classList.toggle("up", up); c.title = t(up ? "conn.connected" : "conn.reconnecting"); c.setAttribute("aria-label", c.title);
  });
  es.addEventListener("hello", () => setConn(true));
  es.onerror = () => setConn(false);
  es.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    const r = S.rooms.find(x => x.name === m.room);
    if (r) r.last_message_id = m.id;
    if (S.room?.name === m.room) {
      appendMessage(m);
      if (m.mentions.includes(S.settings.human_name)) api("POST", "/api/inbox/read", { room: m.room }).catch(() => {});
    } else {
      S.newCount[m.room] = (S.newCount[m.room] || 0) + 1;
      if (r && m.mentions.includes(S.settings.human_name)) { r.unread = (r.unread || 0) + 1; toast(t("msg.mentioned_you", { author: m.author, room: m.room })); }
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

// ------------------------------------------------------------------ per-browser preferences
function pref(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value) localStorage.setItem(key, value); else localStorage.removeItem(key);
  } catch { return null; }
}
function setFont(step) {
  const size = Math.max(12, Math.min(18, (+pref("at.fs") || 14) + step));
  document.documentElement.style.setProperty("--fs", size + "px");
  pref("at.fs", size === 14 ? "" : String(size));
}

// ------------------------------------------------------------------ page events
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
  $("#btn-toggle-panel").addEventListener("click", () => {
    const hidden = $("#app").classList.toggle("no-panel");
    $("#btn-toggle-panel").setAttribute("aria-expanded", String(!hidden)); pref("at.nopanel", hidden ? "1" : "");
  });
  $("#btn-new-room").addEventListener("click", openNewRoom);
  $("#rail-new-room").addEventListener("click", openNewRoom);
  $("#collapse").addEventListener("click", () => { $("#app").classList.add("collapsed"); pref("at.collapsed", "1"); });
  $("#expand").addEventListener("click", () => { $("#app").classList.remove("collapsed"); pref("at.collapsed", ""); });
  $("#font-down").addEventListener("click", () => setFont(-1));
  $("#font-up").addEventListener("click", () => setFont(1));
  $("#btn-find").addEventListener("click", () => {
    const box = $("#find"); box.hidden = !box.hidden;
    $("#btn-find").setAttribute("aria-expanded", String(!box.hidden));
    if (!box.hidden) $("#search").focus(); else { $("#search").value = ""; renderRooms(); renderAgents(); }
  });
  $("#search").addEventListener("input", () => { renderRooms(); renderAgents(); });
  $$(".folder[data-section]").forEach(f => {
    const sec = f.dataset.section, list = $("#" + sec);
    const apply = closed => { f.classList.toggle("closed", closed); list.classList.toggle("closed", closed); };
    apply(pref("at.closed." + sec) === "1");
    const toggle = () => { const c = !f.classList.contains("closed"); apply(c); pref("at.closed." + sec, c ? "1" : ""); };
    f.addEventListener("click", toggle);
    f.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
  });
  if (pref("at.collapsed") === "1") $("#app").classList.add("collapsed");
  if (pref("at.nopanel") === "1") { $("#app").classList.add("no-panel"); $("#btn-toggle-panel").setAttribute("aria-expanded", "false"); }
  setFont(0);
  $("#room-form").addEventListener("submit", submitRoom);
  $("#btn-add-member").addEventListener("click", openAddMember);
  $("#member-form").addEventListener("submit", submitMember);
  $("#btn-tasks").addEventListener("click", () => openAllTasks().catch(fail));
  $("#all-tasks-filter").addEventListener("change", () => openAllTasks().catch(fail));
  $("#btn-spawns").addEventListener("click", openSpawns);
  $("#spawn-form").addEventListener("submit", submitSpawn);
  $("#search").addEventListener("keydown", e => {
    if (e.key === "Enter" && e.target.value.trim()) doSearch(e.target.value.trim()).catch(fail);
    if (e.key === "Escape") $("#btn-find").click();
  });
  $$("[data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));
  window.addEventListener("hashchange", () => { const n = decodeURIComponent(location.hash.slice(1)); if (n && n !== S.room?.name) selectRoom(n); });
  setInterval(() => loadAgents().catch(() => {}), 30000);
}

applyI18n();
bind();
loadAll().then(connect).catch(fail);
