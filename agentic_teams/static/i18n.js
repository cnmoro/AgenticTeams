"use strict";
// UI translations (plain JS, no dependencies). English is the default; Portuguese (pt) is optional.
// Language order: ?lang=xx in the URL, then localStorage "at.lang", then the server setting, then "en".
(function (root) {
  const I18N = {
    en: {
      // page
      "title.app": "AgenticTeams",
      "title.login": "Sign in · AgenticTeams",
      // login
      "login.email": "E-mail",
      "login.password": "Password",
      "login.submit": "Sign in",
      "login.failed": "Could not sign in.",
      "login.unavailable": "Server unavailable.",
      // sidebar
      "conn.connecting": "connecting…",
      "conn.connected": "connected",
      "conn.reconnecting": "reconnecting…",
      "logout": "Log out",
      "search.placeholder": "Search all rooms…",
      "rooms": "Rooms",
      "rooms.new": "New room",
      "rooms.mentions": "mentions of you",
      "rooms.new_messages": "new messages",
      "agents": "Agents",
      "agents.you": "you (human)",
      "agents.online": "online (watcher active)",
      "agents.seen": "seen {when}",
      "agents.never": "never seen",
      "agents.rooms": "rooms: {rooms}",
      "agents.none": "no agents registered",
      "agents.legacy": "+ {n} from agents.txt",
      "agents.legacy_title": "authors imported from agents.txt",
      "tasks.all": "All tasks",
      "spawns": "Subagents",
      // room header
      "room.add_member": "+ member",
      "room.add_member_title": "Add agent to room",
      "room.toggle_panel": "Tasks",
      "room.toggle_panel_title": "Show/hide tasks",
      "member.all": "receives all messages",
      "member.none": "direct mentions only",
      "member.mentions": "receives mentions",
      // composer
      "composer.placeholder": "Message as human — @ mentions an agent, @all wakes the room. Enter sends, Shift+Enter adds a line.",
      "composer.placeholder_room": "Message #{room} as human — @ mentions an agent, @{all} wakes the room. Enter sends, Shift+Enter adds a line.",
      "composer.legacy": "Legacy room: read-only (imported from agents.txt).",
      "composer.send": "Send",
      "reply.to": "Replying to #{id} from {author}: {text}",
      // messages
      "msg.human": "HUMAN",
      "msg.human_title": "message from the human user",
      "msg.cross": "from outside",
      "msg.cross_title": "sent by an agent outside the room",
      "msg.in_reply_to": "↪ in reply to #{id}",
      "msg.reply": "Reply",
      "msg.reply_title": "Reply to this message",
      "msg.task": "Task",
      "msg.task_title": "Create a task from this message",
      "msg.empty": "No messages in this room yet.",
      "msg.not_loaded": "Message #{id} is not loaded in this room.",
      "msg.mentioned_you": "{author} mentioned you in #{room}",
      // mention menu
      "mention.all": "wakes every member of the room",
      "mention.outside": "outside",
      "mention.outside_title": "not a member of this room; receives it anyway",
      // task panel
      "tasks.room": "Room tasks",
      "tasks.new_placeholder": "New task…",
      "tasks.no_assignee": "no assignee",
      "tasks.create": "Create",
      "tasks.show_done": "show finished",
      "tasks.outside_room": " (outside the room)",
      "tasks.none": "No tasks.",
      "tasks.by": "by {who}",
      "tasks.source": "source #{id}",
      "tasks.linked": "linked to message #{id} from {author}",
      "tasks.filter.open": "open",
      "tasks.filter.all": "all",
      "tasks.filter.done": "done",
      "status.open": "open",
      "status.in_progress": "in progress",
      "status.done": "done",
      "status.cancelled": "cancelled",
      // room kinds
      "kind.general": "general",
      "kind.project": "project",
      "kind.topic": "topic",
      "kind.legacy": "legacy",
      // new room dialog
      "room.new_title": "New room",
      "room.name": "Name (a-z, 0-9, -)",
      "room.title": "Title",
      "room.description": "Description",
      "room.kind": "Kind",
      "room.members": "Members",
      "room.no_agents": "No agents registered yet.",
      "cancel": "Cancel",
      "close": "Close",
      "create": "Create",
      // add member dialog
      "member.title": "Add agent to room",
      "member.notify": "Notifications",
      "member.notify.mentions": "mentions only (default)",
      "member.notify.all": "every message in the room",
      "member.notify.none": "none (direct mentions only)",
      "member.add": "Add",
      "member.all_in": "Every agent is already in this room.",
      // subagents dialog
      "spawn.intro": "Starts a new <code>claude --bg</code> session in an allowed folder. The session joins the team under the chosen name and gets the hook that wakes it when mentioned. Allowed roots: ",
      "spawn.folder": "Folder",
      "spawn.name": "Name",
      "spawn.rooms": "Rooms",
      "spawn.rooms_placeholder": "general, proj-x",
      "spawn.mode": "Mode",
      "spawn.prompt": "Task (prompt)",
      "spawn.start": "Start subagent",
      "spawn.history": "Requests and history",
      "spawn.none": "No subagents yet.",
      "spawn.in": " in ",
      "spawn.line": " — {status} · requested by {who} · mode {mode} · {when}",
      "spawn.approve": "Approve and start",
      "spawn.reject": "Reject",
      "spawn.result": "Subagent #{id}: {status}",
      "spawn.no_roots": "none (spawning disabled; set spawn_roots)",
      "spawn_status.pending": "pending",
      "spawn_status.approved": "approved",
      "spawn_status.started": "started",
      "spawn_status.error": "error",
      "spawn_status.rejected": "rejected",
      // search
      "search.title": "Search",
      "search.results": "Search: “{q}” ({n})",
      "search.none": "Nothing found.",
      // errors
      "error": "Error: {msg}",
      "session_expired": "session expired",
    },
    pt: {
      "title.app": "AgenticTeams",
      "title.login": "Entrar · AgenticTeams",
      "login.email": "E-mail",
      "login.password": "Senha",
      "login.submit": "Entrar",
      "login.failed": "Não foi possível entrar.",
      "login.unavailable": "Servidor indisponível.",
      "conn.connecting": "conectando…",
      "conn.connected": "conectado",
      "conn.reconnecting": "reconectando…",
      "logout": "Sair",
      "search.placeholder": "Buscar em todas as salas…",
      "rooms": "Salas",
      "rooms.new": "Nova sala",
      "rooms.mentions": "menções a você",
      "rooms.new_messages": "mensagens novas",
      "agents": "Agentes",
      "agents.you": "você (humano)",
      "agents.online": "online (vigia ativo)",
      "agents.seen": "visto {when}",
      "agents.never": "nunca visto",
      "agents.rooms": "salas: {rooms}",
      "agents.none": "nenhum agente registrado",
      "agents.legacy": "+ {n} do agents.txt",
      "agents.legacy_title": "autores importados do agents.txt",
      "tasks.all": "Todas as tarefas",
      "spawns": "Subagentes",
      "room.add_member": "+ membro",
      "room.add_member_title": "Adicionar agente à sala",
      "room.toggle_panel": "Tarefas",
      "room.toggle_panel_title": "Mostrar/ocultar tarefas",
      "member.all": "recebe todas as mensagens",
      "member.none": "só menção direta",
      "member.mentions": "recebe menções",
      "composer.placeholder": "Mensagem como humano — @ menciona um agente, @todos acorda a sala. Enter envia, Shift+Enter quebra linha.",
      "composer.placeholder_room": "Mensagem em #{room} como humano — @ menciona um agente, @{all} acorda a sala. Enter envia, Shift+Enter quebra linha.",
      "composer.legacy": "Sala legado: somente leitura (importada do agents.txt).",
      "composer.send": "Enviar",
      "reply.to": "Respondendo a #{id} de {author}: {text}",
      "msg.human": "HUMANO",
      "msg.human_title": "mensagem do usuário humano",
      "msg.cross": "de fora",
      "msg.cross_title": "enviada por agente de fora da sala",
      "msg.in_reply_to": "↪ em resposta a #{id}",
      "msg.reply": "Responder",
      "msg.reply_title": "Responder a esta mensagem",
      "msg.task": "Tarefa",
      "msg.task_title": "Criar tarefa a partir desta mensagem",
      "msg.empty": "Nenhuma mensagem nesta sala ainda.",
      "msg.not_loaded": "Mensagem #{id} não está carregada nesta sala.",
      "msg.mentioned_you": "{author} mencionou você em #{room}",
      "mention.all": "acorda todos os membros da sala",
      "mention.outside": "fora",
      "mention.outside_title": "não é membro desta sala; recebe mesmo assim",
      "tasks.room": "Tarefas da sala",
      "tasks.new_placeholder": "Nova tarefa…",
      "tasks.no_assignee": "sem responsável",
      "tasks.create": "Criar",
      "tasks.show_done": "mostrar concluídas",
      "tasks.outside_room": " (fora da sala)",
      "tasks.none": "Nenhuma tarefa.",
      "tasks.by": "por {who}",
      "tasks.source": "origem #{id}",
      "tasks.linked": "vinculada à mensagem #{id} de {author}",
      "tasks.filter.open": "abertas",
      "tasks.filter.all": "todas",
      "tasks.filter.done": "feitas",
      "status.open": "aberta",
      "status.in_progress": "em andamento",
      "status.done": "feita",
      "status.cancelled": "cancelada",
      "kind.general": "geral",
      "kind.project": "projeto",
      "kind.topic": "tema",
      "kind.legacy": "legado",
      "room.new_title": "Nova sala",
      "room.name": "Nome (a-z, 0-9, -)",
      "room.title": "Título",
      "room.description": "Descrição",
      "room.kind": "Tipo",
      "room.members": "Membros",
      "room.no_agents": "Nenhum agente registrado ainda.",
      "cancel": "Cancelar",
      "close": "Fechar",
      "create": "Criar",
      "member.title": "Adicionar agente à sala",
      "member.notify": "Notificação",
      "member.notify.mentions": "só menções (padrão)",
      "member.notify.all": "todas as mensagens da sala",
      "member.notify.none": "nenhuma (só menção direta)",
      "member.add": "Adicionar",
      "member.all_in": "Todos os agentes já estão nesta sala.",
      "spawn.intro": "Inicia uma nova sessão <code>claude --bg</code> numa pasta permitida. A sessão entra no time com o nome escolhido e recebe o hook que a acorda quando for mencionada. Raízes permitidas: ",
      "spawn.folder": "Pasta",
      "spawn.name": "Nome",
      "spawn.rooms": "Salas",
      "spawn.rooms_placeholder": "general, proj-x",
      "spawn.mode": "Modo",
      "spawn.prompt": "Tarefa (prompt)",
      "spawn.start": "Iniciar subagente",
      "spawn.history": "Pedidos e histórico",
      "spawn.none": "Nenhum subagente ainda.",
      "spawn.in": " em ",
      "spawn.line": " — {status} · pedido por {who} · modo {mode} · {when}",
      "spawn.approve": "Aprovar e iniciar",
      "spawn.reject": "Recusar",
      "spawn.result": "Subagente #{id}: {status}",
      "spawn.no_roots": "nenhuma (invocação desativada; configure spawn_roots)",
      "spawn_status.pending": "pendente",
      "spawn_status.approved": "aprovado",
      "spawn_status.started": "iniciado",
      "spawn_status.error": "erro",
      "spawn_status.rejected": "recusado",
      "search.title": "Busca",
      "search.results": "Busca: “{q}” ({n})",
      "search.none": "Nada encontrado.",
      "error": "Erro: {msg}",
      "session_expired": "sessão expirada",
    },
  };
  const LOCALES = { en: "en-US", pt: "pt-BR" };
  const HTML_LANG = { en: "en", pt: "pt-BR" };

  function norm(lang) {
    const l = String(lang || "").toLowerCase().slice(0, 2);
    return I18N[l] ? l : null;
  }
  function override() {
    const q = norm(new URLSearchParams(location.search).get("lang"));
    if (q) { try { localStorage.setItem("at.lang", q); } catch {} return q; }
    try { return norm(localStorage.getItem("at.lang")); } catch { return null; }
  }

  let lang = override() || "en";

  // Server setting; ignored when the URL or localStorage already chose a language.
  function setLang(serverLang) {
    lang = override() || norm(serverLang) || "en";
    document.documentElement.lang = HTML_LANG[lang];
    return lang;
  }
  function currentLang() { return lang; }
  function locale() { return LOCALES[lang]; }

  function t(key, vars) {
    let s = I18N[lang][key] ?? I18N.en[key] ?? key;
    if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
    return s;
  }

  function applyI18n(rootEl = document) {
    for (const e of rootEl.querySelectorAll("[data-i18n]")) e.textContent = t(e.dataset.i18n);
    for (const e of rootEl.querySelectorAll("[data-i18n-html]")) e.innerHTML = t(e.dataset.i18nHtml);
    for (const e of rootEl.querySelectorAll("[data-i18n-placeholder]")) e.placeholder = t(e.dataset.i18nPlaceholder);
    for (const e of rootEl.querySelectorAll("[data-i18n-title]")) e.title = t(e.dataset.i18nTitle);
    for (const e of rootEl.querySelectorAll("[data-i18n-aria-label]")) e.setAttribute("aria-label", t(e.dataset.i18nAriaLabel));
  }

  Object.assign(root, { I18N, t, applyI18n, setLang, currentLang, locale });
  document.documentElement.lang = HTML_LANG[lang];
})(window);
