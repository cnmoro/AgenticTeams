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
    st, _ = env.req("POST", "/api/rooms/geral/messages", {"body": "x"}, env.admin,
                    headers={"Origin": "http://evil.example.com"})
    assert st == 403
    tok = env.register("alfa")
    # agents cannot register other agents
    assert env.req("POST", "/api/agents", {"name": "zeta", "folder": "/tmp"}, tok)[0] == 403


def test_register_and_identity(env):
    tok = env.register("vendas", "/srv/projetos/loja-vendas")
    st, me = env.req("GET", "/api/me", token=tok)
    assert me["handle"] == "vendas@/srv/projetos/loja-vendas"
    assert me["rooms"] == ["geral"] and me["last_seen"]
    assert env.req("POST", "/api/agents", {"name": "todos", "folder": "/x"}, env.admin)[0] == 400
    assert env.req("POST", "/api/agents", {"name": "Bad Name"}, env.admin)[0] == 400
    # same name from another folder needs force
    assert env.req("POST", "/api/agents", {"name": "vendas", "folder": "/outra"}, env.admin)[0] == 409


def test_mentions_wake_only_who_is_mentioned(env):
    a, b, c = env.register("alfa"), env.register("beta"), env.register("gama")
    st, room = env.req("POST", "/api/rooms", {"name": "proj-a", "members": ["beta", "gama"]}, a)
    assert st == 200 and {m["name"] for m in room["members"]} == {"alfa", "beta", "gama"}
    env.req("POST", "/api/rooms/proj-a/messages", {"body": "@beta pode revisar? (cc fulano@exemplo.com)"}, a)
    assert [m["reason"] for m in inbox(env, b)] == ["mention"]
    assert [m for m in inbox(env, c) if m["room"] == "proj-a" and m["kind"] == "chat"] == []
    assert inbox(env, a) == [] or all(m["author"] != "alfa" for m in inbox(env, a))
    # @todos wakes the whole room except the author
    env.req("POST", "/api/rooms/proj-a/messages", {"body": "@todos vou reiniciar a VPN"}, a)
    assert inbox(env, c, unread=1)[-1]["reason"] == "room"
    # notify=all receives everything in the room
    env.req("POST", "/api/rooms/proj-a/join", {"notify": "all"}, c)
    env.req("POST", "/api/rooms/proj-a/messages", {"body": "nota sem menção"}, a)
    assert inbox(env, c, unread=1)[-1]["body"] == "nota sem menção"
    assert inbox(env, b, unread=1)[-1]["body"] != "nota sem menção"


def test_cross_room_mention_and_human(env):
    a, b = env.register("alfa"), env.register("beta")
    env.req("POST", "/api/rooms", {"name": "sala-a"}, a)
    env.req("POST", "/api/rooms", {"name": "sala-b"}, b)
    # alfa asks beta from a room beta is not in
    st, m = env.req("POST", "/api/rooms/sala-a/messages", {"body": "@beta aplica esta correção no teu painel?"}, a)
    assert m["mentions"] == ["beta"]
    assert inbox(env, b)[-1]["reason"] == "cross"
    # alfa posts directly in sala-b (not a member): marked as cross
    st, m = env.req("POST", "/api/rooms/sala-b/messages", {"body": "pedido de fora"}, a)
    assert m["meta"].get("cross") is True
    # human message without mention notifies every member, and is marked as human
    env.req("POST", "/api/rooms/sala-b/messages", {"body": "bom dia"}, env.admin)
    last = inbox(env, b)[-1]
    assert last["author_kind"] == "human" and last["reason"] == "humano"
    # mentioning the human creates a notification for the UI
    env.req("POST", "/api/rooms/sala-a/messages", {"body": "@humano preciso do seu ok"}, a)
    st, me = env.req("GET", "/api/me", token=env.admin)
    assert me["unread"] >= 1


def test_mark_read(env):
    a, b = env.register("alfa"), env.register("beta")
    env.req("POST", "/api/rooms/geral/messages", {"body": "@beta 1"}, a)
    env.req("POST", "/api/rooms/geral/messages", {"body": "@beta 2"}, a)
    assert len(inbox(env, b, unread=1)) == 2
    assert len(inbox(env, b, unread=1, mark_read=1)) == 2
    assert inbox(env, b, unread=1) == []


def test_inbox_long_poll(env):
    a, b = env.register("alfa"), env.register("beta")
    cursor = env.req("GET", "/api/inbox", token=b)[1]["cursor"]

    def later():
        time.sleep(0.7)
        env.req("POST", "/api/rooms/geral/messages", {"body": "@beta acorda"}, a)

    threading.Thread(target=later).start()
    t0 = time.time()
    items = inbox(env, b, after=cursor, wait=10)
    assert items and items[0]["body"] == "@beta acorda"
    assert 0.5 < time.time() - t0 < 5


def test_tasks(env):
    a, b = env.register("alfa"), env.register("beta")
    st, msg = env.req("POST", "/api/rooms/geral/messages", {"body": "alguém pode limpar o disco do servidor de produção?"}, a)
    st, t = env.req("POST", "/api/tasks", {"room": "geral", "title": "Limpar disco", "assignee": "beta",
                                           "source_message_id": msg["id"]}, a)
    assert st == 200 and t["status"] == "aberta" and t["assignee"] == "beta" and t["source_message_id"] == msg["id"]
    got = inbox(env, b)[-1]
    assert got["kind"] == "task" and got["meta"]["task_id"] == t["id"] and got["reply_to"] == msg["id"]
    st, t2 = env.req("PATCH", f"/api/tasks/{t['id']}", {"status": "feita", "note": "liberei 3 GB"}, b)
    assert t2["status"] == "feita"
    upd = inbox(env, a)[-1]
    assert "aberta → feita" in upd["body"] and "liberei 3 GB" in upd["body"]
    assert env.req("PATCH", f"/api/tasks/{t['id']}", {"status": "pronta"}, b)[0] == 400
    st, lst = env.req("GET", "/api/tasks?status=abertas", token=a)
    assert lst == []
    st, lst = env.req("GET", "/api/tasks?assignee=beta", token=a)
    assert [x["id"] for x in lst] == [t["id"]]


def test_spawn_requires_approval_for_agents(env):
    a = env.register("alfa")
    proj = env.roots / "proj"
    proj.mkdir()
    st, sp = env.req("POST", "/api/spawns", {"folder": str(proj), "name": "revisor", "prompt": "revise o README",
                                             "rooms": ["geral"]}, a)
    assert st == 200 and sp["status"] == "pendente"
    assert not (env.fakebin / "argv.txt").exists()
    assert env.req("POST", f"/api/spawns/{sp['id']}/approve", token=a)[0] == 403  # only the human
    st, sp = env.req("POST", f"/api/spawns/{sp['id']}/approve", token=env.admin)
    assert st == 200 and sp["status"] == "iniciado", sp
    assert "bg-session-1234" in sp["output"]
    argv = (env.fakebin / "argv.txt").read_text().split("\n----\n")
    assert argv[0] == "--bg" and "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    hooks = json.loads(argv[argv.index("--settings") + 1])["hooks"]
    cmd = hooks["Stop"][0]["hooks"][0]
    assert cmd["asyncRewake"] is True and "at-watch" in cmd["command"] and "AT_AGENT=revisor" in cmd["command"]
    assert 'Você é o agente "revisor"' in argv[-2] and "revise o README" in argv[-2]
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
    body.update(permission_mode="plan", name="humano")
    assert env.req("POST", "/api/spawns", body, env.admin)[0] in (400, 409)
    body.update(name="ok-1")
    st, sp = env.req("POST", "/api/spawns", body, env.admin)
    assert st == 200 and sp["status"] == "iniciado"  # the human starts directly


def test_legacy_room_is_read_only_and_import(env, tmp_path):
    f = tmp_path / "agents.txt"
    f.write_text(
        "[2026-10-27 15:00:38] AGENTE-BETA: VPN conectada.\n"
        "[2026-11-06 10:01:53 -0300] AGENTE-GAMA: proposta:\n"
        "  BACKEND: x\n"
        "  FRONT: y\n"
        "[2026-11-10 11:19] AGENTE-ALFA: @AGENTE-BETA ok\n")
    created = legacy.import_file(env.store, str(f))
    assert [m["author"] for m in created] == ["agente-beta", "agente-gama", "agente-alfa"]
    assert created[1]["body"] == "proposta:\n  BACKEND: x\n  FRONT: y"
    assert created[0]["created_at"] == "2026-10-27T15:00:38-03:00"
    assert legacy.import_file(env.store, str(f)) == []  # idempotent
    with open(f, "a") as fh:
        fh.write("[2026-11-24 22:27] AGENTE-DELTA: Terminei.\n")
    assert [m["body"] for m in legacy.import_file(env.store, str(f))] == ["Terminei."]
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
    env.req("POST", "/api/rooms/geral/messages", {"body": "evento"}, a)
    line = resp.readline()
    assert line == b"event: message\n"
    assert b'"evento"' in resp.readline()
    conn.close()
