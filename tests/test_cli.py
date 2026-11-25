"""CLI, hook watcher, project installer and MCP server, run as real subprocesses."""
import json
import os
import subprocess
import sys
import threading
import time

from conftest import REPO

AT = [sys.executable, str(REPO / "bin" / "at")]


def run(*args, stdin=None, cwd=None, check=True, env_extra=None):
    env = dict(os.environ, **(env_extra or {}))
    p = subprocess.run([*AT, *args], input=stdin, capture_output=True, text=True, cwd=cwd, env=env, timeout=30)
    if check:
        assert p.returncode == 0, p.stderr
    return p


def test_cli_full_flow(env):
    run("register", "alfa", "--folder", "/tmp/a", "--desc", "teste A")
    run("register", "beta", "--folder", "/tmp/b")
    assert "alfa@/tmp/a" in run("--as", "alfa", "whoami").stdout
    run("--as", "alfa", "room", "create", "proj-x", "--members", "beta", "--desc", "Project X")
    out = run("--as", "alfa", "say", "-r", "proj-x", "@beta", "can", "you", "look?").stdout
    assert "mentions: beta" in out
    # multi-line message from stdin
    run("--as", "alfa", "say", "-r", "proj-x", "-", stdin="line 1\nline 2 @beta\n")
    p = run("--as", "alfa", "say", "-r", "proj-x", "x" * 300)
    assert "hint: long message on a single line" in p.stderr
    assert "hint" not in run("--as", "alfa", "say", "-r", "proj-x", "-", stdin="a\n" + "x" * 300).stderr
    inbox = run("--as", "beta", "inbox").stdout
    assert "#proj-x" in inbox and "<mention>" in inbox and "    line 2 @beta" in inbox
    assert "no new messages" in run("--as", "beta", "inbox").stdout  # marked as read
    task = json.loads(run("--json", "--as", "beta", "task", "add", "Review", "deploy", "-r", "proj-x",
                          "--to", "alfa", "--from-msg", "4").stdout)
    assert task["assignee"] == "alfa" and task["source_message_id"] == 4
    run("--as", "alfa", "task", "set", str(task["id"]), "done", "--note", "finished")
    assert "no tasks" in run("--as", "alfa", "task", "list", "--mine").stdout
    assert "[done]" in run("--as", "alfa", "task", "list", "--all").stdout
    run("--human", "say", "-r", "proj-x", "good morning")
    hist = run("--as", "alfa", "read", "proj-x").stdout
    assert "[HUMAN] human: good morning" in hist
    assert "proj-x" in run("--as", "beta", "search", "look").stdout
    # identity via environment variable
    assert "beta@/tmp/b" in run("whoami", env_extra={"AT_AGENT": "beta"}).stdout
    p = run("say", "hi", check=False)
    assert p.returncode != 0 and "I do not know who you are" in p.stderr + p.stdout


def test_cli_errors_are_readable(env):
    run("register", "alfa")
    p = run("--as", "alfa", "say", "-r", "no-such-room", "hi", check=False)
    assert p.returncode == 1 and "room not found" in p.stderr
    p = run("--as", "alfa", "say", "hi", check=False, env_extra={"AT_URL": "http://127.0.0.1:9"})
    assert p.returncode == 1 and "unavailable" in p.stderr


def test_install_into_project(env, tmp_path):
    proj = tmp_path / "my-project"
    (proj / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    old_hook = {"type": "command", "command": "AGENTS_FILE=/x python3 /h/agents_watch.py AGENTE-X",
                "asyncRewake": True, "timeout": 604800}
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash(ls)"]},
        "hooks": {"Stop": [{"hooks": [old_hook]}]}}))
    out = run("install", "--project-dir", str(proj), "--name", "myproj", "--rooms", "general").stdout
    summary = json.loads(out)
    assert "warning" in summary  # the old agents.txt hook is kept and reported
    cfg = json.loads((proj / ".claude" / "settings.local.json").read_text())
    assert cfg["permissions"] == {"allow": ["Bash(ls)"]}
    cmds = [h["command"] for e in cfg["hooks"]["Stop"] for h in e["hooks"]]
    assert any("agents_watch.py" in c for c in cmds) and any("at-watch" in c for c in cmds)
    assert cfg["hooks"]["SessionStart"][0]["hooks"][0]["asyncRewake"] is True
    ident = json.loads((proj / ".claude" / "agentic-teams.local.json").read_text())
    assert ident["name"] == "myproj"
    exclude = (proj / ".git" / "info" / "exclude").read_text()
    assert ".claude/agentic-teams.local.json" in exclude
    # identity comes from the project file when running inside the project
    assert f"myproj@{proj}" in run("whoami", cwd=proj).stdout
    # installing again does not duplicate the hook
    run("install", "--project-dir", str(proj), "--name", "myproj")
    cfg = json.loads((proj / ".claude" / "settings.local.json").read_text())
    assert sum("at-watch" in h["command"] for e in cfg["hooks"]["Stop"] for h in e["hooks"]) == 1
    # remove leaves the old hook alone
    run("install", "--project-dir", str(proj), "--remove")
    cfg = json.loads((proj / ".claude" / "settings.local.json").read_text())
    assert "SessionStart" not in cfg["hooks"]
    assert [h["command"] for e in cfg["hooks"]["Stop"] for h in e["hooks"]] == [old_hook["command"]]
    assert not (proj / ".claude" / "agentic-teams.local.json").exists()


def watch(name, session, timeout=20):
    env = dict(os.environ, AT_AGENT=name)
    return subprocess.Popen([sys.executable, str(REPO / "bin" / "at-watch")], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def test_watch_wakes_on_mention_only(env):
    run("register", "alfa")
    run("register", "beta")
    # first run of a session with nothing unread: keeps waiting
    p = watch("beta", "s1")
    p.stdin.write(json.dumps({"session_id": "s1"}))
    p.stdin.close()
    time.sleep(1.0)
    run("--as", "alfa", "say", "general notice without mention")
    time.sleep(1.0)
    assert p.poll() is None, "message without mention must not wake"
    run("--as", "alfa", "say", "@beta I need you")
    p.wait(15)
    err = p.stderr.read()
    assert p.returncode == 2
    assert "@beta I need you" in err and "<<'EOF'" in err and "--as beta say" in err
    head, _, ctx = err.partition("Recent context of #general")
    assert "1 message(s)" in head and "general notice" not in head and "general notice" in ctx  # context, not a wake
    assert "#general: @alfa" in err  # roster of the rooms, to know whom to mention
    # next run of the same session continues from the cursor and keeps waiting
    p2 = watch("beta", "s1")
    p2.stdin.write(json.dumps({"session_id": "s1"}))
    p2.stdin.close()
    time.sleep(1.0)
    assert p2.poll() is None
    run("--human", "say", "@beta and the human?")
    p2.wait(15)
    err = p2.stderr.read()
    assert p2.returncode == 2 and "[HUMAN] human" in err
    # second watcher in the same session exits at once (lock)
    run("--as", "beta", "inbox")  # nothing unread, so the first run of s2 keeps waiting
    p3 = watch("beta", "s2")
    p3.stdin.write(json.dumps({"session_id": "s2"}))
    p3.stdin.close()
    time.sleep(0.5)
    p4 = watch("beta", "s2")
    p4.stdin.write(json.dumps({"session_id": "s2"}))
    p4.stdin.close()
    assert p4.wait(10) == 0
    p3.kill()


def test_watch_first_run_wakes_with_unread(env):
    run("register", "alfa")
    run("register", "beta")
    run("--as", "alfa", "say", "@beta message while you were away")
    p = watch("beta", "nova")
    out, err = p.communicate(json.dumps({"session_id": "nova"}), timeout=10)
    assert p.returncode == 2 and "while you were away" in err


def test_watch_unregistered_is_silent(env):
    p = watch("ninguem", "s9")
    out, err = p.communicate(json.dumps({"session_id": "s9"}), timeout=10)
    assert p.returncode == 0 and err == ""


def test_mcp_server(env):
    run("register", "alfa")
    run("register", "beta")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "at_say", "arguments": {"room": "general", "body": "@beta via MCP"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "at_read", "arguments": {"room": "nada"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "bogus"},
    ]
    p = subprocess.run([sys.executable, str(REPO / "bin" / "at-mcp")], input="\n".join(json.dumps(m) for m in msgs),
                       capture_output=True, text=True, timeout=30, env=dict(os.environ, AT_AGENT="alfa"))
    resp = [json.loads(l) for l in p.stdout.splitlines()]
    assert [r["id"] for r in resp] == [1, 2, 3, 4, 5]
    assert resp[0]["result"]["serverInfo"]["name"] == "agentic-teams"
    assert "at_say" in [t["name"] for t in resp[1]["result"]["tools"]]
    assert "mentions: beta" in resp[2]["result"]["content"][0]["text"]
    assert resp[3]["result"]["isError"] is True
    assert resp[4]["error"]["code"] == -32601
    assert "via MCP" in run("--as", "beta", "inbox").stdout


def test_install_picks_name_from_folder(env, tmp_path):
    p1 = tmp_path / "a" / "Store Sales_AI"
    p2 = tmp_path / "b" / "Store Sales_AI"
    for p in (p1, p2):
        p.mkdir(parents=True)
    out = json.loads(run("install", cwd=p1).stdout)
    assert out["agent"] == f"store-sales_ai@{p1}"
    # same folder again: keeps the name
    assert json.loads(run("install", "--project-dir", str(p1)).stdout)["agent"].startswith("store-sales_ai@")
    # another folder with the same basename gets a suffix
    assert json.loads(run("install", "--project-dir", str(p2)).stdout)["agent"] == f"store-sales_ai-2@{p2}"


def test_watch_brings_recent_conversation_as_context(env):
    for n in ("alfa", "beta", "gama"):
        run("register", n)
    run("--as", "alfa", "room", "create", "proj", "--members", "beta,gama")
    run("--as", "gama", "say", "-r", "proj", "old message gama already knows")
    run("--as", "gama", "inbox")
    run("--human", "say", "-r", "proj", "@alfa @beta the placeholder is misaligned")
    run("--as", "alfa", "say", "-r", "proj", "-", stdin="Fixed in Sales:\n- line-height 22px\n- padding 11px 6px\n")
    run("--as", "beta", "say", "-r", "proj", "Applied the same in Inventory, commit abc123.")
    run("--as", "gama", "inbox")  # the human message above woke gama too; mark it read
    p = watch("gama", "ctx")
    p.stdin.write(json.dumps({"session_id": "ctx"}))
    p.stdin.close()
    time.sleep(1.0)
    run("--human", "say", "-r", "proj", "@gama look at what we just discussed and apply the improvements")
    p.wait(15)
    err = p.stderr.read()
    assert p.returncode == 2
    head, _, ctx = err.partition("Recent context of #proj")
    assert "look at what we just discussed" in head
    assert "line-height 22px" in ctx and "commit abc123" in ctx and "placeholder is misaligned" in ctx
    assert "old message gama already knows" in ctx  # window starts at gama's own last message
    assert "Room created" not in ctx


def test_thread_and_read_before(env):
    run("register", "alfa")
    run("register", "beta")
    a = json.loads(run("--json", "--as", "alfa", "say", "first question").stdout)["id"]
    b = json.loads(run("--json", "--as", "beta", "say", "--reply", str(a), "answer").stdout)["id"]
    run("--as", "alfa", "say", "outside the thread")
    c = json.loads(run("--json", "--as", "alfa", "say", "--reply", str(b), "rejoinder").stdout)["id"]
    out = run("--as", "beta", "thread", str(b)).stdout
    assert "first question" in out and "rejoinder" in out and "outside the thread" not in out
    before = run("--as", "beta", "read", "general", "--before", str(c), "--peek").stdout
    assert "outside the thread" in before and "rejoinder" not in before


def test_watch_brings_earlier_request_from_the_caller(env):
    run("register", "alfa")
    run("register", "gama")
    run("--as", "alfa", "room", "create", "proj", "--members", "gama")
    request_id = json.loads(run("--json", "--human", "say", "-r", "proj",
                            "@gama change the Send button color to blue and publish to staging").stdout)["id"]
    run("--as", "gama", "say", "-r", "proj", "--reply", str(request_id), "Doing it now.")
    run("--as", "gama", "task", "add", "Blue Send button", "-r", "proj", "--to", "gama", "--from-msg", str(request_id))
    for i in range(30):  # the room moves on
        run("--as", "alfa", "say", "-r", "proj", f"side topic {i}")
    run("--as", "gama", "say", "-r", "general", "note in another room")
    run("--as", "gama", "inbox")
    p = watch("gama", "rev")
    p.stdin.write(json.dumps({"session_id": "rev"}))
    p.stdin.close()
    time.sleep(1.0)
    run("--human", "say", "-r", "proj", "@gama can you check whether what I asked you earlier was finished?")
    p.wait(15)
    err = p.stderr.read()
    assert p.returncode == 2
    hist = err.partition("Your earlier conversation with @human")[2]
    assert "change the Send button color to blue" in hist and "Doing it now." in hist
    assert "Your open tasks" in err and "Blue Send button" in err
    assert "CHECK the project" in err
    # the same lookup by hand
    out = run("--as", "gama", "history", "--with", "human").stdout
    assert "change the Send button color" in out and "side topic" not in out
    assert "side topic 3" in run("--as", "gama", "history", "--from", "alfa", "--text", "topic 3").stdout
