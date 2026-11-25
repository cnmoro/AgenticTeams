"""AgenticTeams HTTP server: REST API, SSE stream for the UI, long-poll inbox for hooks.

    python -m agentic_teams.server [--host 127.0.0.1] [--port 8765]
"""
import argparse
import asyncio
import html
import json
import logging
import os
import sys
from pathlib import Path

from aiohttp import web

from . import config, legacy, spawn
from .db import Error, Store, now

STATIC = Path(__file__).resolve().parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
log = logging.getLogger("agentic_teams")


def jerr(msg, status):
    return web.json_response({"error": msg}, status=status)


# --------------------------------------------------------------------------- middleware
@web.middleware
async def guard(request, handler):
    """Host/Origin checks (DNS rebinding, CSRF) and token authentication for /api."""
    app = request.app
    host = (request.host or "").rsplit(":", 1)[0] if not (request.host or "").startswith("[") else "[::1]"
    if host not in app["allowed_hosts"]:
        return jerr(f"host não permitido: {request.host}", 403)
    origin = request.headers.get("Origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        ohost = origin.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
        if ohost not in app["allowed_hosts"]:
            return jerr("origem não permitida", 403)
    if request.path.startswith("/api/") and request.path != "/api/health":
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.query.get("token", "")
        if not token:
            return jerr("token ausente (Authorization: Bearer ...)", 401)
        store: Store = app["store"]
        if token == app["admin_token"]:
            request["who"] = store.human()
            request["admin"] = True
        else:
            who = store.agent_by_token(token)
            if not who:
                return jerr("token inválido", 401)
            request["who"] = who
            request["admin"] = False
            store.touch(who["id"])
    try:
        return await handler(request)
    except Error as e:
        return jerr(str(e), e.status)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        return jerr(f"requisição inválida: {e}", 400)


# --------------------------------------------------------------------------- events
def publish(app, kind, data):
    for q in list(app["subscribers"]):
        try:
            q.put_nowait((kind, data))
        except asyncio.QueueFull:
            pass


async def notify_waiters(app):
    cond: asyncio.Condition = app["cond"]
    async with cond:
        cond.notify_all()


async def post_message(app, room, author, body, kind="chat", reply_to=None, meta=None):
    msg, targets = app["store"].post(room["id"], author, body, kind=kind, reply_to=reply_to, meta=meta)
    publish(app, "message", msg)
    if targets:
        await notify_waiters(app)
        publish(app, "inbox", {"agents": targets})
    return msg


async def body_json(request):
    if not request.can_read_body:
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise Error("corpo JSON deve ser um objeto")
    return data


def need_admin(request):
    if not request.get("admin"):
        raise Error("operação restrita ao usuário humano (token de administrador)", 403)


# --------------------------------------------------------------------------- pages
async def index(request):
    page = (STATIC / "index.html").read_text()
    page = page.replace("__AT_TOKEN__", html.escape(request.app["admin_token"]))
    return web.Response(text=page, content_type="text/html",
                        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                 "Referrer-Policy": "no-referrer"})


async def health(request):
    return web.json_response({"ok": True, "time": now()})


# --------------------------------------------------------------------------- agents
async def me(request):
    s: Store = request.app["store"]
    d = s.agent_dict(request["who"])
    d["admin"] = request["admin"]
    d["unread"] = s.one("SELECT count(*) c FROM notifications WHERE agent_id=? AND read_at IS NULL",
                        (request["who"]["id"],))["c"]
    return web.json_response(d)


async def agents_list(request):
    return web.json_response(request.app["store"].list_agents())


async def agents_register(request):
    need_admin(request)
    data = await body_json(request)
    s: Store = request.app["store"]
    name = (data.get("name") or "").strip().lower()
    ex = s.agent_by_name(name)
    folder = data.get("folder") or None
    if ex and ex["kind"] == "agent" and ex["folder"] and folder and ex["folder"] != folder and not data.get("force"):
        raise Error(f"o nome {name} já pertence ao agente da pasta {ex['folder']}; escolha outro nome ou use force",
                    409)
    agent, token = s.register_agent(name, folder, data.get("description", ""),
                                    rooms=data.get("rooms") or [])
    config.save_agent_token(agent["name"], token)
    d = s.agent_dict(agent)
    if not ex:
        await post_message(request.app, s.room("geral"), s.agent_by_name("sistema"),
                           f"Novo agente no time: {d['handle']}" +
                           (f" — {agent['description']}" if agent["description"] else ""), kind="system")
    publish(request.app, "agent", d)
    return web.json_response({"agent": d, "token": token})


# --------------------------------------------------------------------------- rooms
async def rooms_list(request):
    s: Store = request.app["store"]
    mine = request.query.get("mine") == "1"
    return web.json_response(s.list_rooms(request["who"]["id"], only_member=mine))


async def rooms_create(request):
    data = await body_json(request)
    s: Store = request.app["store"]
    room = s.create_room(data.get("name"), data.get("title"), data.get("description", ""),
                         data.get("kind", "projeto"), request["who"]["id"], data.get("members") or [])
    d = s.room_dict(room)
    publish(request.app, "room", d)
    await post_message(request.app, room, request["who"],
                       f"Sala criada: #{room['name']}" + (f" — {room['description']}" if room["description"] else ""),
                       kind="system")
    return web.json_response(d)


async def room_get(request):
    s: Store = request.app["store"]
    return web.json_response(s.room_dict(s.room(request.match_info["room"]), request["who"]["id"]))


def target_agent(request, data):
    """The agent an operation acts on: self, or anyone when called by the human."""
    s: Store = request.app["store"]
    name = data.get("agent")
    if name and name != request["who"]["name"]:
        need_admin(request)
        a = s.agent_by_name(name.lstrip("@").lower())
        if not a:
            raise Error(f"agente não encontrado: {name}", 404)
        return a
    return request["who"]


async def room_join(request):
    data = await body_json(request)
    s: Store = request.app["store"]
    room = s.room(request.match_info["room"])
    a = target_agent(request, data)
    s.join(room["id"], a["id"], data.get("notify"))
    d = s.room_dict(room)
    publish(request.app, "room", d)
    return web.json_response(d)


async def room_leave(request):
    data = await body_json(request)
    s: Store = request.app["store"]
    room = s.room(request.match_info["room"])
    a = target_agent(request, data)
    s.leave(room["id"], a["id"])
    d = s.room_dict(room)
    publish(request.app, "room", d)
    return web.json_response(d)


# --------------------------------------------------------------------------- messages
async def messages_list(request):
    s: Store = request.app["store"]
    room = s.room(request.match_info["room"])
    qy = request.query
    msgs = s.messages(room["id"], after=int(qy.get("after", 0)), before=int(qy["before"]) if qy.get("before") else None,
                      limit=int(qy.get("limit", 100)))
    if qy.get("mark_read") == "1":
        s.mark_read(request["who"]["id"], room_id=room["id"])
    return web.json_response(msgs)


async def messages_post(request):
    s: Store = request.app["store"]
    room = s.room(request.match_info["room"])
    if room["kind"] == "legado":
        raise Error("a sala legado é somente leitura", 403)
    data = await body_json(request)
    who = request["who"]
    meta = {}
    if who["kind"] == "agent":
        member = s.one("SELECT 1 FROM members WHERE room_id=? AND agent_id=?", (room["id"], who["id"]))
        if not member:
            meta["cross"] = True  # posted from outside the room
    msg = await post_message(request.app, room, who, data.get("body", ""), reply_to=data.get("reply_to"), meta=meta)
    return web.json_response(msg)


async def message_get(request):
    s: Store = request.app["store"]
    return web.json_response(s.message_dict(s.get_message(int(request.match_info["id"]))))


async def search(request):
    return web.json_response(request.app["store"].search(request.query.get("q", ""), int(request.query.get("limit", 50))))


# --------------------------------------------------------------------------- inbox
async def inbox(request):
    """Notifications of the caller. `wait=N` long-polls up to N seconds for new ones."""
    s: Store = request.app["store"]
    who = request["who"]
    qy = request.query
    after = int(qy.get("after", 0))
    unread = qy.get("unread") == "1"
    limit = int(qy.get("limit", 100))
    wait = min(float(qy.get("wait", 0)), 120)
    items = s.inbox(who["id"], after, unread, limit)
    if not items and wait > 0:
        cond: asyncio.Condition = request.app["cond"]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while not items:
            left = deadline - loop.time()
            if left <= 0:
                break
            try:
                async with cond:
                    await asyncio.wait_for(cond.wait(), timeout=left)
            except asyncio.TimeoutError:
                pass
            items = s.inbox(who["id"], after, unread, limit)
        s.touch(who["id"])
    if qy.get("mark_read") == "1" and items:
        s.mark_read(who["id"], upto=max(i["notification_id"] for i in items))
    return web.json_response({"items": items, "cursor": s.last_notification_id(who["id"])})


async def inbox_read(request):
    data = await body_json(request)
    s: Store = request.app["store"]
    room_id = s.room(data["room"])["id"] if data.get("room") else None
    n = s.mark_read(request["who"]["id"], upto=data.get("upto"), room_id=room_id)
    publish(request.app, "inbox", {"agents": [request["who"]["id"]]})
    return web.json_response({"marked": n})


# --------------------------------------------------------------------------- tasks
def task_line(t):
    to = f" → @{t['assignee']}" if t["assignee"] else " (sem responsável)"
    return f"tarefa #{t['id']} «{t['title']}»{to}"


async def tasks_list(request):
    s: Store = request.app["store"]
    qy = request.query
    room_id = s.room(qy["room"])["id"] if qy.get("room") else None
    assignee = None
    if qy.get("assignee"):
        a = s.agent_by_name(qy["assignee"].lstrip("@").lower())
        if not a:
            raise Error(f"agente não encontrado: {qy['assignee']}", 404)
        assignee = a["id"]
    return web.json_response(s.list_tasks(room_id, assignee, qy.get("status")))


async def tasks_create(request):
    data = await body_json(request)
    s: Store = request.app["store"]
    room = s.room(data.get("room") or "geral")
    t = s.create_task(room["id"], request["who"], data.get("title"), data.get("body", ""), data.get("assignee"),
                      data.get("source_message_id"))
    td = s.task_dict(t)
    text = f"Nova {task_line(td)}"
    if td["body"]:
        text += f"\n{td['body']}"
    if td["source_message_id"]:
        text += f"\n(origem: mensagem #{td['source_message_id']})"
    await post_message(request.app, room, request["who"], text, kind="task",
                       reply_to=td["source_message_id"], meta={"task_id": td["id"], "event": "created"})
    publish(request.app, "task", td)
    return web.json_response(td)


async def tasks_update(request):
    data = await body_json(request)
    s: Store = request.app["store"]
    before = s.task_dict(s.get_task(int(request.match_info["id"])))
    t = s.update_task(before["id"], status=data.get("status"), assignee=data.get("assignee"),
                      title=data.get("title"), body=data.get("body"))
    td = s.task_dict(t)
    changes = []
    if td["status"] != before["status"]:
        changes.append(f"{before['status']} → {td['status']}")
    if td["assignee"] != before["assignee"]:
        changes.append(f"responsável: {before['assignee'] or '—'} → @{td['assignee']}" if td["assignee"]
                       else "sem responsável")
    if td["title"] != before["title"]:
        changes.append("título alterado")
    note = data.get("note")
    if changes or note:
        text = f"Tarefa #{td['id']} «{td['title']}»: {'; '.join(changes) or 'comentário'}"
        if note:
            text += f"\n{note}"
        watchers = {td["creator"], before["assignee"], td["assignee"]} - {request["who"]["name"], None}
        extra = [f"@{w}" for w in sorted(watchers) if f"@{w}" not in text]
        if extra:
            text += "\n" + " ".join(extra)
        await post_message(request.app, s.room(td["room"]), request["who"], text, kind="task",
                           meta={"task_id": td["id"], "event": "updated"})
    publish(request.app, "task", td)
    return web.json_response(td)


# --------------------------------------------------------------------------- spawns
async def spawns_list(request):
    return web.json_response(request.app["store"].list_spawns())


async def spawns_create(request):
    data = await body_json(request)
    app = request.app
    s: Store = app["store"]
    st = app["settings"]
    folder = spawn.validate_folder(data.get("folder", ""), st["spawn_roots"])
    mode = spawn.validate_mode(data.get("permission_mode"), st["spawn_permission_modes"])
    name = (data.get("name") or "").strip().lower()
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        raise Error("informe o prompt do subagente")
    rooms = [r.lstrip("#").lower() for r in (data.get("rooms") or [])]
    for r in rooms:
        s.room(r)
    if s.agent_by_name(name):
        raise Error(f"já existe um participante chamado {name}; escolha outro nome", 409)
    from .db import NAME_RE, RESERVED
    if not NAME_RE.match(name) or name in RESERVED or name == s.human_name:
        raise Error("nome inválido para o subagente (a-z, 0-9, - ou _)")
    direct = request["admin"] or st["agents_can_spawn"]
    sp = s.create_spawn(request["who"]["id"], name, folder, prompt, rooms, mode, "aprovado" if direct else "pendente")
    if direct:
        sp = await execute_spawn(app, sp["id"], request["who"])
    else:
        human = s.human()
        await post_message(app, s.room(rooms[0] if rooms else "geral"), request["who"],
                           f"@{human['name']} pedido de subagente #{sp['id']}: «{name}» em {folder} "
                           f"(modo {mode}). Aprove ou recuse na interface (Subagentes).\nPrompt: {prompt[:500]}",
                           kind="system", meta={"spawn_id": sp["id"]})
    d = s.spawn_dict(sp)
    publish(app, "spawn", d)
    return web.json_response(d)


async def execute_spawn(app, sid, decided_by):
    s: Store = app["store"]
    st = app["settings"]
    sp = s.get_spawn(sid)
    recent = s.one("SELECT count(*) c FROM spawns WHERE status='iniciado' AND updated_at > ?",
                   (_hour_ago(),))["c"]
    if recent >= int(st["max_spawns_per_hour"]):
        return s.set_spawn(sid, "erro", f"limite de {st['max_spawns_per_hour']} subagentes por hora atingido",
                           decided_by["id"])
    folder = spawn.validate_folder(sp["folder"], st["spawn_roots"])  # re-check: config may have changed
    rooms = json.loads(sp["rooms"])
    requester = s.one("SELECT name FROM agents WHERE id=?", (sp["requested_by"],))["name"]
    agent, token = s.register_agent(sp["agent_name"], folder, f"Subagente iniciado por {requester}",
                                    spawned_by=sp["requested_by"], rooms=rooms)
    config.save_agent_token(agent["name"], token)
    settings = spawn.hook_settings(agent["name"], app["public_url"], config.home())
    argv = spawn.build_argv(st["claude_bin"], agent["name"], sp["permission_mode"], settings,
                            spawn.full_prompt(agent["name"], folder, requester, rooms, sp["prompt"]))
    ok, out = await asyncio.get_running_loop().run_in_executor(None, spawn.run, argv, folder)
    sp = s.set_spawn(sid, "iniciado" if ok else "erro", out, decided_by["id"])
    room = s.room(rooms[0] if rooms else "geral")
    text = (f"Subagente «{agent['name']}» iniciado em {folder} por {requester} (modo {sp['permission_mode']})."
            if ok else f"Falha ao iniciar o subagente «{agent['name']}» em {folder}: {out[:500]}")
    await post_message(app, room, s.agent_by_name("sistema"), text, kind="system", meta={"spawn_id": sid})
    publish(app, "agent", s.agent_dict(agent))
    return sp


def _hour_ago():
    from datetime import datetime, timedelta
    return (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds")


async def spawns_decide(request):
    need_admin(request)
    s: Store = request.app["store"]
    sid = int(request.match_info["id"])
    sp = s.get_spawn(sid)
    if sp["status"] != "pendente":
        raise Error(f"pedido #{sid} não está pendente ({sp['status']})", 409)
    if request.match_info["action"] == "approve":
        sp = await execute_spawn(request.app, sid, request["who"])
    else:
        sp = s.set_spawn(sid, "recusado", decided_by=request["who"]["id"])
        rooms = json.loads(sp["rooms"])
        await post_message(request.app, s.room(rooms[0] if rooms else "geral"), request["who"],
                           f"Pedido de subagente #{sid} («{sp['agent_name']}») recusado.", kind="system")
    d = s.spawn_dict(sp)
    publish(request.app, "spawn", d)
    return web.json_response(d)


async def settings_get(request):
    st = request.app["settings"]
    return web.json_response({
        "human_name": request.app["store"].human_name,
        "spawn_roots": st["spawn_roots"],
        "spawn_permission_modes": [m for m in st["spawn_permission_modes"] if m not in spawn.FORBIDDEN_MODES],
        "agents_can_spawn": st["agents_can_spawn"],
        "legacy_file": st["legacy_file"],
        "url": request.app["public_url"],
    })


# --------------------------------------------------------------------------- SSE
async def stream(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"})
    await resp.prepare(request)
    q = asyncio.Queue(maxsize=1000)
    request.app["subscribers"].add(q)
    try:
        await resp.write(b"event: hello\ndata: {}\n\n")
        while True:
            try:
                kind, data = await asyncio.wait_for(q.get(), timeout=20)
                payload = json.dumps(data, ensure_ascii=False)
                await resp.write(f"event: {kind}\ndata: {payload}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        request.app["subscribers"].discard(q)
    return resp


# --------------------------------------------------------------------------- legacy bridge
async def legacy_loop(app):
    path = app["settings"]["legacy_file"]
    while True:
        try:
            for msg in legacy.import_file(app["store"], path):
                publish(app, "message", msg)
        except Exception:  # keep the bridge alive; log and retry
            log.exception("falha ao importar %s", path)
        await asyncio.sleep(5)


async def on_startup(app):
    if app["settings"].get("legacy_file"):
        app["legacy_task"] = asyncio.create_task(legacy_loop(app))


async def on_cleanup(app):
    t = app.get("legacy_task")
    if t:
        t.cancel()


async def on_shutdown(app):
    for q in list(app["subscribers"]):
        q.put_nowait(("bye", {}))


# --------------------------------------------------------------------------- app
def make_app(db_path=None, settings=None, public_url=None, extra_hosts=()):
    st = config.load_settings()
    st.setdefault("max_spawns_per_hour", 6)
    if settings:
        st.update(settings)
    app = web.Application(middlewares=[guard], client_max_size=1 << 20)
    app["settings"] = st
    app["store"] = Store(db_path or config.home() / "teams.db", st.get("human_name") or "humano")
    app["admin_token"] = config.admin_token(create=True)
    app["subscribers"] = set()
    app["cond"] = asyncio.Condition()
    app["public_url"] = public_url or config.server_url()
    app["allowed_hosts"] = LOCAL_HOSTS | set(extra_hosts)
    r = app.router
    r.add_get("/", index)
    r.add_static("/static/", STATIC)
    r.add_get("/api/health", health)
    r.add_get("/api/me", me)
    r.add_get("/api/settings", settings_get)
    r.add_get("/api/agents", agents_list)
    r.add_post("/api/agents", agents_register)
    r.add_get("/api/rooms", rooms_list)
    r.add_post("/api/rooms", rooms_create)
    r.add_get("/api/rooms/{room}", room_get)
    r.add_post("/api/rooms/{room}/join", room_join)
    r.add_post("/api/rooms/{room}/leave", room_leave)
    r.add_get("/api/rooms/{room}/messages", messages_list)
    r.add_post("/api/rooms/{room}/messages", messages_post)
    r.add_get("/api/messages/{id:\\d+}", message_get)
    r.add_get("/api/search", search)
    r.add_get("/api/inbox", inbox)
    r.add_post("/api/inbox/read", inbox_read)
    r.add_get("/api/tasks", tasks_list)
    r.add_post("/api/tasks", tasks_create)
    r.add_patch("/api/tasks/{id:\\d+}", tasks_update)
    r.add_get("/api/spawns", spawns_list)
    r.add_post("/api/spawns", spawns_create)
    r.add_post("/api/spawns/{id:\\d+}/{action:approve|reject}", spawns_decide)
    r.add_get("/api/stream", stream)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app


def main(argv=None):
    p = argparse.ArgumentParser(description="Servidor AgenticTeams")
    p.add_argument("--host", default=os.environ.get("AT_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("AT_PORT", 8765)))
    p.add_argument("--db", help="arquivo SQLite (padrão: $AT_HOME/teams.db)")
    p.add_argument("--allow-host", action="append", default=[],
                   help="nome de host extra aceito no cabeçalho Host (ex.: ao expor na rede; veja README)")
    p.add_argument("--import-legacy", metavar="ARQUIVO",
                   help="importa (somente leitura) o agents.txt para a sala 'legado' e sai")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"AVISO: escutando em {a.host}. Qualquer máquina que alcance esta porta e tenha o token "
              f"pode ler e escrever no chat. Veja README (Segurança).", file=sys.stderr)
    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host}:{a.port}"
    app = make_app(a.db, public_url=os.environ.get("AT_URL") or url, extra_hosts=a.allow_host)
    if a.import_legacy:
        n = len(legacy.import_file(app["store"], a.import_legacy))
        print(f"{n} mensagens importadas de {a.import_legacy} para #legado")
        return 0
    print(f"AgenticTeams em {url}  (dados: {config.home()})", flush=True)
    web.run_app(app, host=a.host, port=a.port, print=None, access_log=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
