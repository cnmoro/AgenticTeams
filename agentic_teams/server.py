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
import warnings
from pathlib import Path

from aiohttp import web

from . import auth, config, legacy, spawn
from .i18n import LANGUAGES, t as _t
from .db import Error, Store, now

STATIC = Path(__file__).resolve().parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
log = logging.getLogger("agentic_teams")
warnings.filterwarnings("ignore", category=web.NotAppKeyWarning)


def jerr(msg, status):
    return web.json_response({"error": msg}, status=status)


# --------------------------------------------------------------------------- middleware
PUBLIC = {"/login", "/api/login", "/api/health", "/static/login.js", "/static/style.css", "/static/i18n.js"}


def host_of(request):
    h = request.host or ""
    return "[::1]" if h.startswith("[") else h.rsplit(":", 1)[0]


def same_origin(request):
    """Origin must be present and equal to the Host the browser used (blocks CSRF)."""
    origin = request.headers.get("Origin", "")
    return bool(origin) and origin.split("://", 1)[-1].rstrip("/") == request.host


def session_of(request):
    return request.app["sessions"].get(request.cookies.get(auth.COOKIE))


@web.middleware
async def guard(request, handler):
    """Host/Origin checks (DNS rebinding, CSRF) and authentication.

    Two ways in: a Bearer token (agents, CLI, `at --human`) or the login cookie of the web UI.
    """
    app = request.app
    if host_of(request) not in app["allowed_hosts"]:
        return jerr(f"host not allowed: {request.host}", 403)
    origin = request.headers.get("Origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        ohost = origin.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
        if ohost not in app["allowed_hosts"]:
            return jerr("origin not allowed", 403)
    path = request.path
    store: Store = app["store"]
    if path not in PUBLIC:
        auth_h = request.headers.get("Authorization", "")
        token = auth_h[7:] if auth_h.startswith("Bearer ") else ""
        if not token and path == "/api/stream":
            token = request.query.get("token", "")  # SSE clients that cannot send headers
        sess = None if token else session_of(request)
        if token:
            if token == app["admin_token"]:
                request["who"], request["admin"] = store.human(), True
            else:
                who = store.agent_by_token(token)
                if not who:
                    return jerr("invalid token", 401)
                request["who"], request["admin"] = who, False
                store.touch(who["id"])
        elif sess:
            if request.method not in ("GET", "HEAD") and not same_origin(request):
                return jerr("origem ausente ou diferente (CSRF)", 403)
            request["who"], request["admin"], request["user"] = store.human(), True, sess["email"]
        elif path == "/":
            raise web.HTTPFound("/login")
        elif path.startswith("/api/"):
            return jerr("not authenticated: log in or use Authorization: Bearer", 401)
        elif not path.startswith("/static/"):
            raise web.HTTPFound("/login")
    try:
        return await handler(request)
    except Error as e:
        return jerr(str(e), e.status)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        return jerr(f"invalid request: {e}", 400)


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


def tr(app, key, **kw):
    """Text in the language of this install (config "language")."""
    return _t(app["settings"]["language"], key, **kw)


def need_admin(request):
    if not request.get("admin"):
        raise Error("only the human user (admin token) may do this", 403)


# --------------------------------------------------------------------------- pages
async def index(request):
    return web.FileResponse(STATIC / "index.html", headers=PAGE_HEADERS)


PAGE_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"}


async def login_page(request):
    if session_of(request):
        raise web.HTTPFound("/")
    return web.FileResponse(STATIC / "login.html", headers=PAGE_HEADERS)


async def login(request):
    if not same_origin(request):
        return jerr("origem ausente ou diferente", 403)
    app = request.app
    ip = request.remote or "?"
    wait = app["limiter"].begin(ip)
    if wait:
        msg = f"{-(-wait // 60)} min" if wait > 60 else f"{wait} s"
        return web.json_response({"error": f"Muitas tentativas. Tente de novo em {msg}."}, status=429,
                                 headers={"Retry-After": str(wait)})
    email, data = None, {}
    try:
        data = await body_json(request)
        email = await asyncio.get_running_loop().run_in_executor(
            None, auth.check_credentials, data.get("email"), data.get("password"))
    finally:
        app["limiter"].end(ip, bool(email))
    if not email:
        log.warning("login falhou para %r de %s", str(data.get("email", ""))[:100], ip)
        return jerr("Invalid email or password", 401)
    log.info("login de %s a partir de %s", email, ip)
    resp = web.json_response({"ok": True})
    resp.set_cookie(auth.COOKIE, app["sessions"].create(email), max_age=app["sessions"].ttl_s, path="/",
                    httponly=True, samesite="Strict", secure=request.secure)
    return resp


async def logout(request):
    if not same_origin(request):
        return jerr("origem ausente ou diferente", 403)
    request.app["sessions"].destroy(request.cookies.get(auth.COOKIE))
    resp = web.json_response({"ok": True})
    resp.del_cookie(auth.COOKIE, path="/")
    return resp


async def health(request):
    return web.json_response({"ok": True, "time": now(), "language": request.app["settings"]["language"]})


# --------------------------------------------------------------------------- agents
async def me(request):
    s: Store = request.app["store"]
    d = s.agent_dict(request["who"])
    d["admin"] = request["admin"]
    d["user"] = request.get("user")
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
        raise Error(f"the name {name} belongs to the agent of folder {ex['folder']}; pick another name or use force",
                    409)
    agent, token = s.register_agent(name, folder, data.get("description", ""),
                                    rooms=data.get("rooms") or [])
    config.save_agent_token(agent["name"], token)
    d = s.agent_dict(agent)
    if not ex:
        await post_message(request.app, s.general_room(), s.system(),
                           tr(request.app, "new_agent", handle=d["handle"],
                              desc=f" — {agent['description']}" if agent["description"] else ""), kind="system")
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
                         data.get("kind", "project"), request["who"]["id"], data.get("members") or [])
    d = s.room_dict(room)
    publish(request.app, "room", d)
    invited = [f"@{m['name']}" for m in d["members"] if m["name"] != request["who"]["name"]]
    text = tr(request.app, "room_created", room=room["name"],
              desc=f" — {room['description']}" if room["description"] else "")
    if invited:
        text += "\n" + tr(request.app, "members", names=", ".join(invited))
    await post_message(request.app, room, request["who"], text, kind="system")
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
            raise Error(f"agent not found: {name}", 404)
        return a
    return request["who"]


async def room_join(request):
    data = await body_json(request)
    s: Store = request.app["store"]
    room = s.room(request.match_info["room"])
    a = target_agent(request, data)
    new = s.join(room["id"], a["id"], data.get("notify"))
    d = s.room_dict(room)
    publish(request.app, "room", d)
    if new and a["id"] != request["who"]["id"] and a["kind"] == "agent":
        # tell the agent (this wakes its session) and show it who is there
        others = [f"@{m['name']}" for m in d["members"] if m["name"] != a["name"]]
        await post_message(request.app, room, request["who"],
                           tr(request.app, "added_to_room", agent=a["name"], room=room["name"],
                              desc=f" ({room['description']})" if room["description"] else "",
                              others=", ".join(others) or tr(request.app, "only_you")), kind="system")
    elif new:
        await post_message(request.app, room, s.system(), tr(request.app, "joined_room", agent=a["name"]),
                           kind="system")
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
    if room["kind"] == "legacy":
        raise Error("the legacy room is read-only", 403)
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


async def messages_query(request):
    """GET /api/messages?with=&from=&room=&q=&before=&after=&since=&limit= (see Store.query)."""
    s: Store = request.app["store"]
    qy = request.query

    def agent_id(key):
        if not qy.get(key):
            return None
        a = s.agent_by_name(qy[key].lstrip("@").lower())
        if not a:
            raise Error(f"participant not found: {qy[key]}", 404)
        return a["id"]
    return web.json_response(s.query(
        me_id=request["who"]["id"], with_id=agent_id("with"), author_id=agent_id("from"),
        room_id=s.room(qy["room"])["id"] if qy.get("room") else None, text=qy.get("q"),
        before=qy.get("before"), after=qy.get("after"), since=qy.get("since"), limit=qy.get("limit", 50)))


async def message_thread(request):
    return web.json_response(request.app["store"].thread(int(request.match_info["id"])))


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
        while not items and not request.app["state"]["closing"]:
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
async def tasks_list(request):
    s: Store = request.app["store"]
    qy = request.query
    room_id = s.room(qy["room"])["id"] if qy.get("room") else None
    assignee = None
    if qy.get("assignee"):
        a = s.agent_by_name(qy["assignee"].lstrip("@").lower())
        if not a:
            raise Error(f"agent not found: {qy['assignee']}", 404)
        assignee = a["id"]
    return web.json_response(s.list_tasks(room_id, assignee, qy.get("status")))


async def tasks_create(request):
    data = await body_json(request)
    s: Store = request.app["store"]
    room = s.room(data.get("room") or "general")
    t = s.create_task(room["id"], request["who"], data.get("title"), data.get("body", ""), data.get("assignee"),
                      data.get("source_message_id"))
    td = s.task_dict(t)
    to = tr(request.app, "task_to", name=td["assignee"]) if td["assignee"] else tr(request.app, "task_unassigned")
    text = tr(request.app, "new_task", id=td["id"], title=td["title"], to=to)
    if td["body"]:
        text += f"\n{td['body']}"
    if td["source_message_id"]:
        text += "\n" + tr(request.app, "task_origin", id=td["source_message_id"])
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
        changes.append(f"{tr(request.app, 'status_' + before['status'])} → {tr(request.app, 'status_' + td['status'])}")
    if td["assignee"] != before["assignee"]:
        changes.append(tr(request.app, "assignee_change", old=before["assignee"] or "—",
                          new=f"@{td['assignee']}" if td["assignee"] else tr(request.app, "unassigned")))
    if td["title"] != before["title"]:
        changes.append(tr(request.app, "title_changed"))
    note = data.get("note")
    if changes or note:
        text = tr(request.app, "task_update", id=td["id"], title=td["title"],
                  changes="; ".join(changes) or tr(request.app, "comment"))
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
        raise Error("the subagent needs a prompt")
    rooms = [r.lstrip("#").lower() for r in (data.get("rooms") or [])]
    for r in rooms:
        s.room(r)
    if s.agent_by_name(name):
        raise Error(f"there is already a participant called {name}; pick another name", 409)
    from .db import NAME_RE, RESERVED
    if not NAME_RE.match(name) or name in RESERVED or name == s.human_name:
        raise Error("invalid subagent name (a-z, 0-9, - or _)")
    direct = request["admin"] or st["agents_can_spawn"]
    sp = s.create_spawn(request["who"]["id"], name, folder, prompt, rooms, mode, "approved" if direct else "pending")
    if direct:
        sp = await execute_spawn(app, sp["id"], request["who"])
    else:
        human = s.human()
        await post_message(app, s.room(rooms[0]) if rooms else s.general_room(), request["who"],
                           tr(app, "spawn_request", human=human["name"], id=sp["id"], name=name, folder=folder,
                              mode=mode, prompt=prompt[:500]), kind="system", meta={"spawn_id": sp["id"]})
    d = s.spawn_dict(sp)
    publish(app, "spawn", d)
    return web.json_response(d)


async def execute_spawn(app, sid, decided_by):
    s: Store = app["store"]
    st = app["settings"]
    sp = s.get_spawn(sid)
    recent = s.one("SELECT count(*) c FROM spawns WHERE status='started' AND updated_at > ?",
                   (_hour_ago(),))["c"]
    if recent >= int(st["max_spawns_per_hour"]):
        return s.set_spawn(sid, "error", tr(app, "spawn_limit", n=st["max_spawns_per_hour"]), decided_by["id"])
    folder = spawn.validate_folder(sp["folder"], st["spawn_roots"])  # re-check: config may have changed
    rooms = json.loads(sp["rooms"])
    requester = s.one("SELECT name FROM agents WHERE id=?", (sp["requested_by"],))["name"]
    agent, token = s.register_agent(sp["agent_name"], folder, tr(app, "spawn_by", requester=requester),
                                    spawned_by=sp["requested_by"], rooms=rooms)
    config.save_agent_token(agent["name"], token)
    settings = spawn.hook_settings(agent["name"], app["public_url"], config.home())
    argv = spawn.build_argv(st["claude_bin"], agent["name"], sp["permission_mode"], settings,
                            spawn.full_prompt(agent["name"], folder, requester, rooms, sp["prompt"],
                                              st["language"], s.general_room()["name"]))
    ok, out = await asyncio.get_running_loop().run_in_executor(None, spawn.run, argv, folder)
    if not ok:
        if "not trusted" in out:
            out += "\n\n" + tr(app, "trust_hint", folder=folder)
        s.delete_agent(agent["id"])  # frees the name for a new attempt
        config.agent_token_path(agent["name"]).unlink(missing_ok=True)
    sp = s.set_spawn(sid, "started" if ok else "error", out, decided_by["id"])
    room = s.room(rooms[0]) if rooms else s.general_room()
    text = (tr(app, "spawn_started", name=agent["name"], folder=folder, requester=requester,
               mode=sp["permission_mode"]) if ok
            else tr(app, "spawn_failed", name=agent["name"], folder=folder, output=out[:500]))
    await post_message(app, room, s.system(), text, kind="system", meta={"spawn_id": sid})
    publish(app, "agent", {"name": agent["name"]})
    return sp


def _hour_ago():
    from datetime import datetime, timedelta
    return (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds")


async def spawns_decide(request):
    need_admin(request)
    s: Store = request.app["store"]
    sid = int(request.match_info["id"])
    sp = s.get_spawn(sid)
    if sp["status"] != "pending":
        raise Error(f"request #{sid} is not pending ({sp['status']})", 409)
    if request.match_info["action"] == "approve":
        sp = await execute_spawn(request.app, sid, request["who"])
    else:
        sp = s.set_spawn(sid, "rejected", decided_by=request["who"]["id"])
        rooms = json.loads(sp["rooms"])
        await post_message(request.app, s.room(rooms[0]) if rooms else s.general_room(), request["who"],
                           tr(request.app, "spawn_rejected", id=sid, name=sp["agent_name"]), kind="system")
    d = s.spawn_dict(sp)
    publish(request.app, "spawn", d)
    return web.json_response(d)


async def settings_get(request):
    st = request.app["settings"]
    return web.json_response({
        "human_name": request.app["store"].human_name,
        "language": st["language"],
        "general_room": request.app["store"].general_room()["name"],
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


async def no_stale_static(request, response):
    """Static files (UI code) are revalidated on every load, so a new version shows up at once after an
    update; unchanged files cost a 304."""
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"


async def on_startup(app):
    if app["settings"].get("legacy_file"):
        app["legacy_task"] = asyncio.create_task(legacy_loop(app))


async def on_cleanup(app):
    t = app.get("legacy_task")
    if t:
        t.cancel()


async def on_shutdown(app):
    app["state"]["closing"] = True
    await notify_waiters(app)
    for q in list(app["subscribers"]):
        q.put_nowait(("bye", {}))


# --------------------------------------------------------------------------- app
def make_app(db_path=None, settings=None, public_url=None, extra_hosts=()):
    st = config.load_settings()
    st.setdefault("max_spawns_per_hour", 6)
    if settings:
        st.update(settings)
    app = web.Application(middlewares=[guard], client_max_size=1 << 30)  # no practical limit on message size
    app["settings"] = st
    if st.get("language") not in LANGUAGES:
        st["language"] = "en"
    app["store"] = Store(db_path or config.home() / "teams.db", st.get("human_name") or "human", st["language"],
                         st.get("general_room") or "general")
    app["admin_token"] = config.admin_token(create=True)
    app["sessions"] = auth.Sessions(ttl_s=float(st.get("session_ttl_hours", 168)) * 3600,
                                    idle_s=float(st.get("session_idle_hours", 24)) * 3600)
    app["limiter"] = auth.Limiter()
    app["subscribers"] = set()
    app["state"] = {"closing": False}
    app["cond"] = asyncio.Condition()
    app["public_url"] = public_url or config.server_url()
    app["allowed_hosts"] = LOCAL_HOSTS | set(extra_hosts)
    r = app.router
    r.add_get("/", index)
    r.add_get("/login", login_page)
    r.add_post("/api/login", login)
    r.add_post("/api/logout", logout)
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
    r.add_get("/api/messages", messages_query)
    r.add_get("/api/messages/{id:\\d+}/thread", message_thread)
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
    app.on_response_prepare.append(no_stale_static)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app


async def serve(app, hosts, port, retry_s=30):
    """Listen on every host. The first one (localhost) must work; the others (e.g. the
    Tailscale IP, which may come up after this service) are retried in the background."""
    import signal
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, hosts[0], port).start()
    log.info("listening on %s:%s", hosts[0], port)
    pending = list(hosts[1:])

    async def retry():
        while pending:
            for h in list(pending):
                try:
                    await web.TCPSite(runner, h, port).start()
                    pending.remove(h)
                    log.info("listening on %s:%s", h, port)
                except OSError as e:
                    log.warning("could not listen on %s:%s (%s); retrying in %ss", h, port, e, retry_s)
            if pending:
                await asyncio.sleep(retry_s)

    task = asyncio.create_task(retry())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    task.cancel()
    await runner.cleanup()


def main(argv=None):
    p = argparse.ArgumentParser(description="AgenticTeams server")
    p.add_argument("--host", action="append",
                   help="address to listen on; repeat for several (e.g. --host 127.0.0.1 --host 100.x.y.z). "
                        "The first one must work; the others are retried every 30 s. "
                        "Default: $AT_HOST (comma-separated) or 127.0.0.1")
    p.add_argument("--port", type=int, default=int(os.environ.get("AT_PORT", 8765)))
    p.add_argument("--db", help="SQLite file (default: $AT_HOME/teams.db)")
    p.add_argument("--allow-host", action="append", default=[],
                   help="extra name accepted in the Host header (e.g. the Tailscale MagicDNS name)")
    p.add_argument("--import-legacy", metavar="ARQUIVO",
                   help="import (read-only) an agents.txt into the legacy room and exit")
    a = p.parse_args(argv)
    hosts = a.host or [h.strip() for h in os.environ.get("AT_HOST", "127.0.0.1").split(",") if h.strip()]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    remote = [h for h in hosts if h not in LOCAL_HOSTS]
    if remote:
        print(f"NOTE: also listening on {', '.join(remote)}. Anyone who reaches these addresses gets the login "
              f"page; see README (security model).", file=sys.stderr)
    local = hosts[0] if hosts[0] not in ("0.0.0.0", "::") else "127.0.0.1"
    url = f"http://{local}:{a.port}"
    extra = set(a.allow_host) | {h for h in hosts if h not in ("0.0.0.0", "::")}
    app = make_app(a.db, public_url=os.environ.get("AT_URL") or url, extra_hosts=extra)
    if a.import_legacy:
        n = len(legacy.import_file(app["store"], a.import_legacy))
        print(f"{n} messages imported from {a.import_legacy} into #legacy")
        return 0
    print(f"AgenticTeams on {', '.join(f'http://{h}:{a.port}' for h in hosts)}  (data: {config.home()})", flush=True)
    asyncio.run(serve(app, hosts, a.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
