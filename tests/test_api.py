import json
import threading
import time

from agentic_teams import legacy


def inbox(srv, token, **qs):
    q = "&".join(f"{k}={v}" for k, v in qs.items())
    st, d = srv.req("GET", f"/api/inbox?{q}", token=token)
    assert st == 200, d
    return d["items"]


def test_auth_and_host_checks(env):
    assert env.req("GET", "/api/health")[0] == 200
    assert env.req("GET", "/api/rooms")[0] == 401
    assert env.req("GET", "/api/rooms", token="nope")[0] == 401
    st, _ = env.req("GET", "/api/rooms", token=env.admin, headers={"Host": "evil.example.com"})
    assert st == 403
    st, _ = env.req("POST", "/api/rooms/general/messages", {"body": "x"}, env.admin,
                    headers={"Origin": "http://evil.example.com"})
    assert st == 403
    tok = env.register("alfa")
    # agents cannot register other agents
    assert env.req("POST", "/api/agents", {"name": "zeta", "folder": "/tmp"}, tok)[0] == 403


def test_register_and_identity(env):
    tok = env.register("sales", "/srv/projects/store-sales")
    st, me = env.req("GET", "/api/me", token=tok)
    assert me["handle"] == "sales@/srv/projects/store-sales"
    assert me["rooms"] == ["general"] and me["last_seen"]
    assert env.req("POST", "/api/agents", {"name": "todos", "folder": "/x"}, env.admin)[0] == 400
    assert env.req("POST", "/api/agents", {"name": "Bad Name"}, env.admin)[0] == 400
    # same name from another folder needs force
    assert env.req("POST", "/api/agents", {"name": "sales", "folder": "/other"}, env.admin)[0] == 409


def test_mentions_wake_only_who_is_mentioned(env):
    a, b, c = env.register("alfa"), env.register("beta"), env.register("gama")
    st, room = env.req("POST", "/api/rooms", {"name": "proj-a", "members": ["beta", "gama"]}, a)
    assert st == 200 and {m["name"] for m in room["members"]} == {"alfa", "beta", "gama"}
    env.req("POST", "/api/rooms/proj-a/messages", {"body": "@beta can you review? (cc someone@example.com)"}, a)
    got = inbox(env, b)
    assert got[0]["kind"] == "system" and "@beta" in got[0]["body"]  # room creation tells the members
    assert [m["reason"] for m in got if m["kind"] == "chat"] == ["mention"]
    assert [m for m in inbox(env, c) if m["room"] == "proj-a" and m["kind"] == "chat"] == []
    assert inbox(env, a) == [] or all(m["author"] != "alfa" for m in inbox(env, a))
    # @all wakes the whole room except the author
    env.req("POST", "/api/rooms/proj-a/messages", {"body": "@all restarting the VPN"}, a)
    assert inbox(env, c, unread=1)[-1]["reason"] == "room"
    # notify=all receives everything in the room
    env.req("POST", "/api/rooms/proj-a/join", {"notify": "all"}, c)
    env.req("POST", "/api/rooms/proj-a/messages", {"body": "note without mention"}, a)
    assert inbox(env, c, unread=1)[-1]["body"] == "note without mention"
    assert inbox(env, b, unread=1)[-1]["body"] != "note without mention"


def test_cross_room_mention_and_human(env):
    a, b = env.register("alfa"), env.register("beta")
    env.req("POST", "/api/rooms", {"name": "room-a"}, a)
    env.req("POST", "/api/rooms", {"name": "room-b"}, b)
    # alfa asks beta from a room beta is not in
    st, m = env.req("POST", "/api/rooms/room-a/messages", {"body": "@beta can you apply this fix to your module?"}, a)
    assert m["mentions"] == ["beta"]
    assert inbox(env, b)[-1]["reason"] == "cross"
    # alfa posts directly in room-b (not a member): marked as cross
    st, m = env.req("POST", "/api/rooms/room-b/messages", {"body": "request from outside"}, a)
    assert m["meta"].get("cross") is True
    # human message without mention notifies every member, and is marked as human
    env.req("POST", "/api/rooms/room-b/messages", {"body": "good morning"}, env.admin)
    last = inbox(env, b)[-1]
    assert last["author_kind"] == "human" and last["reason"] == "human"
    # mentioning the human creates a notification for the UI
    env.req("POST", "/api/rooms/room-a/messages", {"body": "@human I need your ok"}, a)
    st, me = env.req("GET", "/api/me", token=env.admin)
    assert me["unread"] >= 1


def test_mark_read(env):
    a, b = env.register("alfa"), env.register("beta")
    env.req("POST", "/api/rooms/general/messages", {"body": "@beta 1"}, a)
    env.req("POST", "/api/rooms/general/messages", {"body": "@beta 2"}, a)
    assert len(inbox(env, b, unread=1)) == 2
    assert len(inbox(env, b, unread=1, mark_read=1)) == 2
    assert inbox(env, b, unread=1) == []


def test_inbox_long_poll(env):
    a, b = env.register("alfa"), env.register("beta")
    cursor = env.req("GET", "/api/inbox", token=b)[1]["cursor"]

    def later():
        time.sleep(0.7)
        env.req("POST", "/api/rooms/general/messages", {"body": "@beta wake up"}, a)

    threading.Thread(target=later).start()
    t0 = time.time()
    items = inbox(env, b, after=cursor, wait=10)
    assert items and items[0]["body"] == "@beta wake up"
    assert 0.5 < time.time() - t0 < 5


def test_tasks(env):
    a, b = env.register("alfa"), env.register("beta")
    st, msg = env.req("POST", "/api/rooms/general/messages", {"body": "can someone clean up the production server disk?"}, a)
    st, t = env.req("POST", "/api/tasks", {"room": "general", "title": "Clean disk", "assignee": "beta",
                                           "source_message_id": msg["id"]}, a)
    assert st == 200 and t["status"] == "open" and t["assignee"] == "beta" and t["source_message_id"] == msg["id"]
    got = inbox(env, b)[-1]
    assert got["kind"] == "task" and got["meta"]["task_id"] == t["id"] and got["reply_to"] == msg["id"]
    st, t2 = env.req("PATCH", f"/api/tasks/{t['id']}", {"status": "done", "note": "freed 3 GB"}, b)
    assert t2["status"] == "done"
    upd = inbox(env, a)[-1]
    assert "open → done" in upd["body"] and "freed 3 GB" in upd["body"]
    assert env.req("PATCH", f"/api/tasks/{t['id']}", {"status": "almost"}, b)[0] == 400
    st, lst = env.req("GET", "/api/tasks?status=open_any", token=a)
    assert lst == []
    st, lst = env.req("GET", "/api/tasks?assignee=beta", token=a)
    assert [x["id"] for x in lst] == [t["id"]]


def test_spawn_requires_approval_for_agents(env):
    a = env.register("alfa")
    proj = env.roots / "proj"
    proj.mkdir()
    st, sp = env.req("POST", "/api/spawns", {"folder": str(proj), "name": "revisor", "prompt": "review the README",
                                             "rooms": ["geral"]}, a)
    assert st == 200 and sp["status"] == "pending"
    assert not (env.fakebin / "argv.txt").exists()
    assert env.req("POST", f"/api/spawns/{sp['id']}/approve", token=a)[0] == 403  # only the human
    st, sp = env.req("POST", f"/api/spawns/{sp['id']}/approve", token=env.admin)
    assert st == 200 and sp["status"] == "started", sp
    assert "bg-session-1234" in sp["output"]
    argv = (env.fakebin / "argv.txt").read_text().split("\n----\n")
    assert argv[0] == "--bg" and "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    hooks = json.loads(argv[argv.index("--settings") + 1])["hooks"]
    cmd = hooks["Stop"][0]["hooks"][0]
    assert cmd["asyncRewake"] is True and "at-watch" in cmd["command"] and "AT_AGENT=revisor" in cmd["command"]
    assert 'You are the agent "revisor"' in argv[-2] and "review the README" in argv[-2]
    assert (env.fakebin / "cwd.txt").read_text().strip() == str(proj.resolve())
    # the new agent exists and has a token
    from agentic_teams import config
    assert config.agent_token("revisor")
    st, agents = env.req("GET", "/api/agents", token=a)
    assert any(x["name"] == "revisor" and x["folder"] == str(proj.resolve()) for x in agents)


def test_spawn_security(env):
    proj = env.roots / "p2"
    proj.mkdir()
    bad = env.tmp / "fora"
    bad.mkdir()
    body = {"folder": str(bad), "name": "x1", "prompt": "oi"}
    assert env.req("POST", "/api/spawns", body, env.admin)[0] == 403
    (env.roots / "link").symlink_to(bad)
    body["folder"] = str(env.roots / "link")
    assert env.req("POST", "/api/spawns", body, env.admin)[0] == 403  # symlink escaping the root
    body.update(folder=str(proj), permission_mode="bypassPermissions")
    assert env.req("POST", "/api/spawns", body, env.admin)[0] == 403
    body.update(permission_mode="plan", name="human")
    assert env.req("POST", "/api/spawns", body, env.admin)[0] in (400, 409)
    body.update(name="ok-1")
    st, sp = env.req("POST", "/api/spawns", body, env.admin)
    assert st == 200 and sp["status"] == "started"  # the human starts directly


def test_legacy_room_is_read_only_and_import(env, tmp_path):
    f = tmp_path / "agents.txt"
    f.write_text(
        "[2026-10-27 14:54:11] AGENTE-ALFA (Claude, session of repo x): I need the VPN\n"
        "[2026-10-27 15:00:38] AGENTE-BETA: VPN connected.\n"
        "[2026-11-06 10:01:53 -0300] AGENTE-GAMA: proposal:\n"
        "  BACKEND: x\n"
        "  FRONT: y\n"
        "[2026-11-10 11:19] AGENTE-ALFA: @AGENTE-BETA ok\n")
    created = legacy.import_file(env.store, str(f))
    assert [m["author"] for m in created] == ["agente-alfa", "agente-beta", "agente-gama", "agente-alfa"]
    assert created[0]["body"] == "I need the VPN"
    assert created[2]["body"] == "proposal:\n  BACKEND: x\n  FRONT: y"
    assert created[1]["created_at"] == "2026-10-27T15:00:38-03:00"
    assert legacy.import_file(env.store, str(f)) == []  # idempotent
    with open(f, "a") as fh:
        fh.write("[2026-11-24 22:27] AGENTE-DELTA: Done.\n")
    assert [m["body"] for m in legacy.import_file(env.store, str(f))] == ["Done."]
    assert env.req("POST", "/api/rooms/legado/messages", {"body": "x"}, env.admin)[0] == 403
    # legacy messages never notify anyone
    assert env.store.one("SELECT count(*) c FROM notifications")["c"] == 0


def test_stream_sends_events(env):
    import http.client
    a = env.register("alfa")
    conn = http.client.HTTPConnection("127.0.0.1", env.port, timeout=10)
    conn.request("GET", f"/api/stream?token={env.admin}")
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.readline().startswith(b"event: hello")
    resp.readline(); resp.readline()
    env.req("POST", "/api/rooms/general/messages", {"body": "event"}, a)
    line = resp.readline()
    assert line == b"event: message\n"
    assert b'"event"' in resp.readline()
    conn.close()


def test_failed_spawn_frees_the_name(env):
    proj = env.roots / "p3"
    proj.mkdir()
    (env.fakebin / "claude").write_text("#!/bin/sh\necho 'Workspace not trusted. Run claude once.' >&2\nexit 1\n")
    body = {"folder": str(proj), "name": "tentativa", "prompt": "oi"}
    st, sp = env.req("POST", "/api/spawns", body, env.admin)
    assert st == 200 and sp["status"] == "error" and "trust prompt" in sp["output"]
    assert env.store.agent_by_name("tentativa") is None
    st, msgs = env.req("GET", "/api/rooms/general/messages", token=env.admin)
    assert "Could not start subagent" in msgs[-1]["body"]
    (env.fakebin / "claude").write_text("#!/bin/sh\necho ok\n")
    st, sp = env.req("POST", "/api/spawns", body, env.admin)
    assert sp["status"] == "started"


def _http(env, method, path, body=None, headers=None):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(env.url + path, data=data, method=method, headers=headers or {})
    if data:
        r.add_header("Content-Type", "application/json")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(r, timeout=10)
    except urllib.error.HTTPError as e:
        resp = e
    return resp.status if hasattr(resp, "status") else resp.code, resp.headers, resp.read().decode()


def test_web_login(env):
    from agentic_teams import auth
    auth.set_user("Owner@Example.com", "test-password-123")
    origin = {"Origin": env.url}
    # the page and the API need a login; the admin token is no longer in the page
    st, h, _ = _http(env, "GET", "/")
    assert st == 302 and h["Location"] == "/login"
    assert _http(env, "GET", "/login")[0] == 200
    assert _http(env, "GET", "/api/rooms")[0] == 401
    # login needs a matching Origin
    assert _http(env, "POST", "/api/login", {"email": "owner@example.com", "password": "test-password-123"})[0] == 403
    st, _, body = _http(env, "POST", "/api/login", {"email": "owner@example.com", "password": "wrong-wrong"}, origin)
    assert st == 401
    st, h, _ = _http(env, "POST", "/api/login", {"email": " OWNER@example.com", "password": "test-password-123"},
                     origin)
    assert st == 200
    cookie = h["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    c = {"Cookie": cookie.split(";", 1)[0]}
    st, _, page = _http(env, "GET", "/", headers=c)
    assert st == 200 and env.admin not in page
    st, _, me = _http(env, "GET", "/api/me", headers=c)
    assert json.loads(me)["kind"] == "human" and json.loads(me)["user"] == "owner@example.com"
    # writes with the cookie need the Origin header (CSRF)
    assert _http(env, "POST", "/api/rooms/general/messages", {"body": "oi"}, c)[0] == 403
    st, _, m = _http(env, "POST", "/api/rooms/general/messages", {"body": "oi"}, {**c, **origin})
    assert st == 200 and json.loads(m)["author_kind"] == "human"
    # changing the password ends the session
    auth.set_user("owner@example.com", "another-pass-12345")
    assert _http(env, "GET", "/api/me", headers=c)[0] == 401
    st, h, _ = _http(env, "POST", "/api/login", {"email": "owner@example.com", "password": "another-pass-12345"}, origin)
    c = {"Cookie": h["Set-Cookie"].split(";", 1)[0]}
    assert _http(env, "POST", "/api/logout", None, {**c, **origin})[0] == 200
    assert _http(env, "GET", "/api/me", headers=c)[0] == 401


def test_login_throttling(env):
    origin = {"Origin": env.url}
    codes = [_http(env, "POST", "/api/login", {"email": "x@y.z", "password": "nada-nada-nada"}, origin)[0]
             for _ in range(6)]
    assert codes == [401] * 5 + [429]


def test_claude_manager_hash_format():
    """Hashes written by claude-manager (Node crypto.scrypt) verify here."""
    import base64, hashlib
    from agentic_teams import auth
    salt = b"0123456789abcdef"
    key = hashlib.scrypt(b"pw-pw-pw-pw-pw", salt=salt, n=32768, r=8, p=1, maxmem=64 << 20, dklen=64)
    stored = f"scrypt$32768$8$1${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"
    assert auth.verify_password("pw-pw-pw-pw-pw", stored)
    assert not auth.verify_password("other", stored)


def test_human_adding_agent_to_room_wakes_it(env):
    a = env.register("alfa")
    env.req("POST", "/api/rooms", {"name": "time-b", "description": "Project B"}, env.admin)
    st, _ = env.req("POST", "/api/rooms/time-b/join", {"agent": "alfa"}, env.admin)
    got = inbox(env, a)
    assert st == 200 and got[-1]["room"] == "time-b" and "you were added to room #time-b" in got[-1]["body"]
    env.req("POST", "/api/rooms/time-b/join", {"agent": "alfa", "notify": "all"}, env.admin)
    assert len(inbox(env, a)) == len(got)  # changing notify mode is not a new membership


def test_reply_wakes_the_author_without_mention(env):
    a, b = env.register("alfa"), env.register("beta")
    st, q = env.req("POST", "/api/rooms/general/messages", {"body": "@beta is the role badge still missing?"}, a)
    st, r = env.req("POST", "/api/rooms/general/messages", {"body": "I applied the badge in app.js, no commit yet.",
                                                          "reply_to": q["id"]}, b)
    got = inbox(env, a)
    assert got[-1]["id"] == r["id"] and got[-1]["reason"] == "reply"
    # replying to the human notifies the human too
    st, h = env.req("POST", "/api/rooms/general/messages", {"body": "@beta publish it"}, env.admin)
    env.req("POST", "/api/rooms/general/messages", {"body": "Published to staging.", "reply_to": h["id"]}, b)
    st, me = env.req("GET", "/api/me", token=env.admin)
    assert me["unread"] >= 1


def test_acknowledgements_do_not_wake_agents(env):
    a, b = env.register("alfa"), env.register("beta")
    st, q = env.req("POST", "/api/rooms/general/messages", {"body": "@beta can you check the disk?"}, a)
    n = len(inbox(env, a))
    for body in ("ok", "@alfa obrigado, valeu!", "Entendido.", "combinado 👍"):
        env.req("POST", "/api/rooms/general/messages", {"body": body, "reply_to": q["id"]}, b)
    assert len(inbox(env, a)) == n
    # a reply with content still wakes, and so does the human saying "ok"
    env.req("POST", "/api/rooms/general/messages", {"body": "ok, but which server?", "reply_to": q["id"]}, b)
    assert len(inbox(env, a)) == n + 1
    env.req("POST", "/api/rooms/general/messages", {"body": "@beta ok", "reply_to": q["id"]}, env.admin)
    assert inbox(env, b)[-1]["author"] == "human"


def test_long_reply_ping_pong_stops_waking(env):
    from agentic_teams.db import MAX_REPLY_CHAIN
    toks = {"alfa": env.register("alfa"), "beta": env.register("beta")}
    st, m = env.req("POST", "/api/rooms/general/messages", {"body": "@beta start of the conversation"}, toks["alfa"])
    woke = []
    for i in range(10):
        who, other = ("beta", "alfa") if i % 2 == 0 else ("alfa", "beta")
        before = len(inbox(env, toks[other]))
        st, m = env.req("POST", "/api/rooms/general/messages", {"body": f"answer {i} with new content",
                                                              "reply_to": m["id"]}, toks[who])
        woke.append(len(inbox(env, toks[other])) > before)
    assert woke[:MAX_REPLY_CHAIN - 1] == [True] * (MAX_REPLY_CHAIN - 1) and not any(woke[MAX_REPLY_CHAIN:])
    # an explicit mention still wakes
    env.req("POST", "/api/rooms/general/messages", {"body": "@beta this still wakes", "reply_to": m["id"]},
            toks["alfa"])
    assert inbox(env, toks["beta"])[-1]["body"] == "@beta this still wakes"


def test_english_defaults(env):
    st, s = env.req("GET", "/api/settings", token=env.admin)
    assert s["language"] == "en" and s["human_name"] == "human" and s["general_room"] == "general"
    assert env.req("GET", "/api/health")[1]["language"] == "en"
    a = env.register("alfa")
    st, msgs = env.req("GET", "/api/rooms/general/messages", token=a)
    assert msgs[-1]["body"].startswith("New agent on the team: alfa@")


def test_portuguese_aliases_still_work(env):
    a, b = env.register("alfa"), env.register("beta")
    assert env.req("POST", "/api/rooms/geral/messages", {"body": "@todos aviso"}, a)[1]["room"] == "general"
    assert inbox(env, b)[-1]["reason"] == "room"
    st, r = env.req("POST", "/api/rooms", {"name": "proj", "kind": "projeto"}, a)
    assert r["kind"] == "project"
    st, t = env.req("POST", "/api/tasks", {"room": "geral", "title": "x", "assignee": "beta"}, a)
    st, t = env.req("PATCH", f"/api/tasks/{t['id']}", {"status": "em_andamento"}, b)
    assert t["status"] == "in_progress"
    st, lst = env.req("GET", "/api/tasks?status=abertas", token=a)
    assert [x["id"] for x in lst] == [t["id"]]
    st, t = env.req("PATCH", f"/api/tasks/{t['id']}", {"status": "feita"}, b)
    assert t["status"] == "done"


def test_portuguese_install_and_migration(tmp_path):
    """An install created by an older (Portuguese) version keeps working after the upgrade."""
    import sqlite3
    from agentic_teams.db import SCHEMA, Store
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executescript("""
        INSERT INTO agents(id,name,kind,created_at) VALUES (1,'sistema','system','x'),(2,'humano','human','x'),
                                                           (3,'alfa','agent','x');
        INSERT INTO rooms(id,name,title,kind,created_at) VALUES (1,'geral','Geral','geral','x'),(2,'p','P','projeto','x');
        INSERT INTO messages(id,room_id,author_id,body,created_at) VALUES (1,1,3,'oi','x');
        INSERT INTO tasks(room_id,title,status,creator_id,created_at,updated_at) VALUES (1,'t','em_andamento',3,'x','x');
        INSERT INTO spawns(requested_by,agent_name,folder,prompt,permission_mode,status,created_at,updated_at)
               VALUES (3,'s','/x','p','plan','pendente','x','x');
        INSERT INTO notifications(agent_id,message_id,reason,created_at) VALUES (3,1,'humano','x');
    """)
    db.commit()
    db.close()
    s = Store(path, human_name="human", lang="pt", general_room="geral")
    assert s.human_name == "humano"  # the existing human keeps its name
    assert s.system()["name"] == "sistema" and s.one("SELECT count(*) c FROM agents WHERE kind='system'")["c"] == 1
    assert s.room("general")["name"] == "geral" and s.room("geral")["kind"] == "general"
    assert s.room("p")["kind"] == "project"
    assert s.list_tasks()[0]["status"] == "in_progress"
    assert s.list_spawns()[0]["status"] == "pending"
    assert s.one("SELECT reason FROM notifications")["reason"] == "human"
    assert s.one("SELECT count(*) c FROM rooms WHERE kind='general'")["c"] == 1  # no second general room


def test_server_notices_in_portuguese(tmp_path, monkeypatch):
    from conftest import Server
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("AT_HOME", str(home))
    srv = Server(home, {"language": "pt", "human_name": "humano", "general_room": "geral", "spawn_roots": []})
    monkeypatch.setenv("AT_URL", srv.url)
    srv.start()
    try:
        a, b = srv.register("alfa"), srv.register("beta")
        st, msgs = srv.req("GET", "/api/rooms/geral/messages", token=a)
        assert msgs[0]["body"].startswith("Novo agente no time: alfa@")
        st, t = srv.req("POST", "/api/tasks", {"room": "geral", "title": "Limpar disco", "assignee": "beta"}, a)
        srv.req("PATCH", f"/api/tasks/{t['id']}", {"status": "done"}, b)
        st, msgs = srv.req("GET", "/api/rooms/geral/messages", token=a)
        assert "Nova tarefa #1" in msgs[-2]["body"] and "aberta → feita" in msgs[-1]["body"]
        assert srv.req("GET", "/api/settings", token=a)[1]["language"] == "pt"
    finally:
        srv.stop()


def test_static_files_are_revalidated(env):
    import urllib.request
    with urllib.request.urlopen(env.url + "/static/app.js", timeout=10) as r:
        assert r.headers["Cache-Control"] == "no-cache" and r.headers["ETag"]
