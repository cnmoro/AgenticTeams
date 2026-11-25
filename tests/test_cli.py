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
    run("--as", "alfa", "room", "create", "proj-x", "--members", "beta", "--desc", "Projeto X")
    out = run("--as", "alfa", "say", "-r", "proj-x", "@beta", "pode", "olhar?").stdout
    assert "menciona: beta" in out
    # multi-line message from stdin
    run("--as", "alfa", "say", "-r", "proj-x", "-", stdin="linha 1\nlinha 2 @beta\n")
    p = run("--as", "alfa", "say", "-r", "proj-x", "x" * 300)
    assert "dica: mensagem longa numa linha" in p.stderr
    assert "dica" not in run("--as", "alfa", "say", "-r", "proj-x", "-", stdin="a\n" + "x" * 300).stderr
    inbox = run("--as", "beta", "inbox").stdout
    assert "#proj-x" in inbox and "<mention>" in inbox and "    linha 2 @beta" in inbox
    assert "nenhuma mensagem nova" in run("--as", "beta", "inbox").stdout  # marked as read
    task = json.loads(run("--json", "--as", "beta", "task", "add", "Revisar", "deploy", "-r", "proj-x",
                          "--to", "alfa", "--from-msg", "4").stdout)
    assert task["assignee"] == "alfa" and task["source_message_id"] == 4
    run("--as", "alfa", "task", "set", str(task["id"]), "done", "--note", "feito")
    assert "nenhuma tarefa" in run("--as", "alfa", "task", "list", "--mine").stdout
    assert "[feita]" in run("--as", "alfa", "task", "list", "--all").stdout
    run("--human", "say", "-r", "proj-x", "bom dia")
    hist = run("--as", "alfa", "read", "proj-x").stdout
    assert "[HUMANO] humano: bom dia" in hist
    assert "proj-x" in run("--as", "beta", "search", "olhar").stdout
    # identity via environment variable
    assert "beta@/tmp/b" in run("whoami", env_extra={"AT_AGENT": "beta"}).stdout
    p = run("say", "oi", check=False)
    assert p.returncode != 0 and "não sei quem você é" in p.stderr + p.stdout


def test_cli_errors_are_readable(env):
    run("register", "alfa")
    p = run("--as", "alfa", "say", "-r", "nao-existe", "oi", check=False)
    assert p.returncode == 1 and "sala não encontrada" in p.stderr
    p = run("--as", "alfa", "say", "oi", check=False, env_extra={"AT_URL": "http://127.0.0.1:9"})
    assert p.returncode == 1 and "indisponível" in p.stderr


def test_install_into_project(env, tmp_path):
    proj = tmp_path / "meu-projeto"
    (proj / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    old_hook = {"type": "command", "command": "AGENTS_FILE=/x python3 /h/agents_watch.py AGENTE-X",
                "asyncRewake": True, "timeout": 604800}
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash(ls)"]},
        "hooks": {"Stop": [{"hooks": [old_hook]}]}}))
    out = run("install", "--project-dir", str(proj), "--name", "meuproj", "--rooms", "geral").stdout
    summary = json.loads(out)
    assert "aviso" in summary  # the old agents.txt hook is kept and reported
    cfg = json.loads((proj / ".claude" / "settings.local.json").read_text())
    assert cfg["permissions"] == {"allow": ["Bash(ls)"]}
    cmds = [h["command"] for e in cfg["hooks"]["Stop"] for h in e["hooks"]]
    assert any("agents_watch.py" in c for c in cmds) and any("at-watch" in c for c in cmds)
    assert cfg["hooks"]["SessionStart"][0]["hooks"][0]["asyncRewake"] is True
    ident = json.loads((proj / ".claude" / "agentic-teams.local.json").read_text())
    assert ident["name"] == "meuproj"
    exclude = (proj / ".git" / "info" / "exclude").read_text()
    assert ".claude/agentic-teams.local.json" in exclude
    # identity comes from the project file when running inside the project
    assert f"meuproj@{proj}" in run("whoami", cwd=proj).stdout
    # installing again does not duplicate the hook
    run("install", "--project-dir", str(proj), "--name", "meuproj")
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
    run("--as", "alfa", "say", "aviso geral sem menção")
    time.sleep(1.0)
    assert p.poll() is None, "message without mention must not wake"
    run("--as", "alfa", "say", "@beta preciso de você")
    p.wait(15)
    err = p.stderr.read()
    assert p.returncode == 2
    assert "@beta preciso de você" in err and "<<'EOF'" in err and "--as beta say" in err
    head, _, ctx = err.partition("Contexto recente de #geral")
    assert "1 mensagem(ns)" in head and "aviso geral" not in head and "aviso geral" in ctx  # context, not a wake
    assert "#geral: @alfa" in err  # roster of the rooms, to know whom to mention
    # next run of the same session continues from the cursor and keeps waiting
    p2 = watch("beta", "s1")
    p2.stdin.write(json.dumps({"session_id": "s1"}))
    p2.stdin.close()
    time.sleep(1.0)
    assert p2.poll() is None
    run("--human", "say", "@beta e o humano?")
    p2.wait(15)
    err = p2.stderr.read()
    assert p2.returncode == 2 and "[HUMANO] humano" in err
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
    run("--as", "alfa", "say", "@beta mensagem enquanto você estava fora")
    p = watch("beta", "nova")
    out, err = p.communicate(json.dumps({"session_id": "nova"}), timeout=10)
    assert p.returncode == 2 and "enquanto você estava fora" in err


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
         "params": {"name": "at_say", "arguments": {"room": "geral", "body": "@beta via MCP"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "at_read", "arguments": {"room": "nada"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "bogus"},
    ]
    p = subprocess.run([sys.executable, str(REPO / "bin" / "at-mcp")], input="\n".join(json.dumps(m) for m in msgs),
                       capture_output=True, text=True, timeout=30, env=dict(os.environ, AT_AGENT="alfa"))
    resp = [json.loads(l) for l in p.stdout.splitlines()]
    assert [r["id"] for r in resp] == [1, 2, 3, 4, 5]
    assert resp[0]["result"]["serverInfo"]["name"] == "agentic-teams"
    assert "at_say" in [t["name"] for t in resp[1]["result"]["tools"]]
    assert "menciona: beta" in resp[2]["result"]["content"][0]["text"]
    assert resp[3]["result"]["isError"] is True
    assert resp[4]["error"]["code"] == -32601
    assert "via MCP" in run("--as", "beta", "inbox").stdout


def test_install_picks_name_from_folder(env, tmp_path):
    p1 = tmp_path / "a" / "Painel Vendas_IA"
    p2 = tmp_path / "b" / "Painel Vendas_IA"
    for p in (p1, p2):
        p.mkdir(parents=True)
    out = json.loads(run("install", cwd=p1).stdout)
    assert out["agente"] == f"painel-vendas_ia@{p1}"
    # same folder again: keeps the name
    assert json.loads(run("install", "--project-dir", str(p1)).stdout)["agente"].startswith("painel-vendas_ia@")
    # another folder with the same basename gets a suffix
    assert json.loads(run("install", "--project-dir", str(p2)).stdout)["agente"] == f"painel-vendas_ia-2@{p2}"


def test_watch_brings_recent_conversation_as_context(env):
    for n in ("alfa", "beta", "gama"):
        run("register", n)
    run("--as", "alfa", "room", "create", "proj", "--members", "beta,gama")
    run("--as", "gama", "say", "-r", "proj", "mensagem antiga que o gama já conhece")
    run("--as", "gama", "inbox")
    run("--human", "say", "-r", "proj", "@alfa @beta o placeholder está desalinhado")
    run("--as", "alfa", "say", "-r", "proj", "-", stdin="Corrigi no Vendas:\n- line-height 22px\n- padding 11px 6px\n")
    run("--as", "beta", "say", "-r", "proj", "Apliquei o mesmo no Estoque, commit abc123.")
    run("--as", "gama", "inbox")  # the human message above woke gama too; mark it read
    p = watch("gama", "ctx")
    p.stdin.write(json.dumps({"session_id": "ctx"}))
    p.stdin.close()
    time.sleep(1.0)
    run("--human", "say", "-r", "proj", "@gama veja o que acabamos de conversar e aplique as melhorias")
    p.wait(15)
    err = p.stderr.read()
    assert p.returncode == 2
    head, _, ctx = err.partition("Contexto recente de #proj")
    assert "veja o que acabamos de conversar" in head
    assert "line-height 22px" in ctx and "commit abc123" in ctx and "placeholder está desalinhado" in ctx
    assert "mensagem antiga que o gama já conhece" in ctx  # window starts at gama's own last message
    assert "Sala criada" not in ctx


def test_thread_and_read_before(env):
    run("register", "alfa")
    run("register", "beta")
    a = json.loads(run("--json", "--as", "alfa", "say", "pergunta inicial").stdout)["id"]
    b = json.loads(run("--json", "--as", "beta", "say", "--reply", str(a), "resposta").stdout)["id"]
    run("--as", "alfa", "say", "fora do fio")
    c = json.loads(run("--json", "--as", "alfa", "say", "--reply", str(b), "tréplica").stdout)["id"]
    out = run("--as", "beta", "thread", str(b)).stdout
    assert "pergunta inicial" in out and "tréplica" in out and "fora do fio" not in out
    before = run("--as", "beta", "read", "geral", "--before", str(c), "--peek").stdout
    assert "fora do fio" in before and "tréplica" not in before


def test_watch_brings_earlier_request_from_the_caller(env):
    run("register", "alfa")
    run("register", "gama")
    run("--as", "alfa", "room", "create", "proj", "--members", "gama")
    pedido = json.loads(run("--json", "--human", "say", "-r", "proj",
                            "@gama troque a cor do botão Enviar para azul e publique na homologação").stdout)["id"]
    run("--as", "gama", "say", "-r", "proj", "--reply", str(pedido), "Vou fazer agora.")
    run("--as", "gama", "task", "add", "Botão Enviar azul", "-r", "proj", "--to", "gama", "--from-msg", str(pedido))
    for i in range(30):  # the room moves on
        run("--as", "alfa", "say", "-r", "proj", f"assunto paralelo {i}")
    run("--as", "gama", "say", "-r", "geral", "nota em outra sala")
    run("--as", "gama", "inbox")
    p = watch("gama", "rev")
    p.stdin.write(json.dumps({"session_id": "rev"}))
    p.stdin.close()
    time.sleep(1.0)
    run("--human", "say", "-r", "proj", "@gama consegue revisar se o que eu te pedi antes foi finalizado?")
    p.wait(15)
    err = p.stderr.read()
    assert p.returncode == 2
    hist = err.partition("Sua conversa anterior com @humano")[2]
    assert "troque a cor do botão Enviar para azul" in hist and "Vou fazer agora." in hist
    assert "Suas tarefas abertas" in err and "Botão Enviar azul" in err
    assert "CONFIRA no projeto" in err
    # the same lookup by hand
    out = run("--as", "gama", "history", "--with", "humano").stdout
    assert "troque a cor do botão" in out and "assunto paralelo" not in out
    assert "assunto paralelo 3" in run("--as", "gama", "history", "--from", "alfa", "--text", "paralelo 3").stdout
