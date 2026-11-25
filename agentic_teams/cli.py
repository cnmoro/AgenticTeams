"""`at`: command line for agents (and humans) in AgenticTeams.

Identity: --as NAME, or $AT_AGENT, or .claude/agentic-teams.local.json in the project.
Server: $AT_URL (default http://127.0.0.1:8765). Data/tokens: $AT_HOME.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from . import config
from .client import PROJECT_FILE, ApiError, Client, project_root, q, resolve_identity

REPO = Path(__file__).resolve().parent.parent
WATCH_MARK = "agentic_teams_watch"


# --------------------------------------------------------------------------- output
def short_time(ts):
    if not ts:
        return "?"
    return ts[:16].replace("T", " ")


def fmt_message(m, show_room=False, lang="en"):
    from .i18n import t
    who = m["author"]
    if m["author_kind"] == "human":
        who = f"{t(lang, 'human_tag')} {who}"
    elif m["author_kind"] == "system" or m["kind"] == "system":
        tag = t(lang, "system_tag")
        who = f"{tag} {who}" if m["author_kind"] == "system" else f"{who} {tag}"
    elif m["kind"] == "task":
        who = f"{who} {t(lang, 'task_tag')}"
    room = f"#{m['room']} " if show_room else ""
    reply = f" (re #{m['reply_to']})" if m.get("reply_to") else ""
    reason = f" <{m['reason']}>" if m.get("reason") else ""
    lines = m["body"].splitlines() or [""]
    head = f"{room}#{m['id']} [{short_time(m['created_at'])}] {who}{reply}{reason}: {lines[0]}"
    return "\n".join([head] + [f"    {l}" for l in lines[1:]])


def fmt_task(t):
    to = f"@{t['assignee']}" if t["assignee"] else "unassigned"
    src = f" (msg #{t['source_message_id']})" if t["source_message_id"] else ""
    return f"#{t['id']} [{t['status']}] #{t['room']} {t['title']} → {to}, created by {t['creator']}{src}"


def out(args, data, text):
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif text:
        print(text)


def read_body(parts):
    text = " ".join(parts)
    if text == "-" or not parts:
        text = sys.stdin.read()
    return text.strip()


# --------------------------------------------------------------------------- commands
def agent_client(args):
    name = resolve_identity(args.as_)
    if not name:
        sys.exit("at: I do not know who you are. Use --as NAME, AT_AGENT=NAME, or run 'at install' in the project.")
    c = Client(agent=name)
    if not c.token:
        sys.exit(f"at: no token for '{name}' in {config.agent_token_path(name)}. Run 'at register {name}'.")
    return c


def human_client(args):
    c = Client(admin=True)
    if not c.token:
        sys.exit(f"at: admin token not found in {config.home()}/admin.token (has the server started yet?)")
    return c


def client_for(args):
    return human_client(args) if args.human else agent_client(args)


def cmd_register(args):
    c = human_client(args)
    folder = os.path.abspath(args.folder) if args.folder else str(project_root())
    rooms = [r for r in (args.rooms or "").split(",") if r]
    res = c.post("/api/agents", {"name": args.name, "folder": folder, "description": args.desc or "",
                                 "rooms": rooms, "force": args.force})
    config.save_agent_token(res["agent"]["name"], res["token"])
    out(args, res["agent"], f"registered: {res['agent']['handle']}  rooms: {', '.join(res['agent']['rooms'])}\n"
                            f"token salvo em {config.agent_token_path(res['agent']['name'])}")


def cmd_whoami(args):
    c = client_for(args)
    me = c.get("/api/me")
    out(args, me, f"{me['handle']} ({me['kind']})  rooms: {', '.join(me['rooms']) or '-'}  unread: {me['unread']}")


def cmd_agents(args):
    c = client_for(args)
    data = c.get("/api/agents")
    lines = []
    for a in data:
        if a["kind"] == "legacy" and not args.all:
            continue
        seen = short_time(a["last_seen"]) if a["last_seen"] else "-"
        lines.append(f"{a['name']:<20} {a['kind']:<6} seen {seen}  {a['folder'] or ''}  rooms: {', '.join(a['rooms'])}")
    out(args, data, "\n".join(lines))


def cmd_rooms(args):
    c = client_for(args)
    data = c.get("/api/rooms", mine="1" if args.mine else None)
    lines = []
    for r in data:
        members = ", ".join(m["name"] + ("*" if m["notify"] == "all" else "") for m in r["members"])
        unread = f"  unread: {r['unread']}" if r.get("unread") else ""
        lines.append(f"#{r['name']:<18} [{r['kind']}] {r['title']} — open tasks: {r['open_tasks']}{unread}\n"
                     f"    members: {members or '-'}")
    out(args, data, "\n".join(lines))


def cmd_room_create(args):
    c = client_for(args)
    members = [m for m in (args.members or "").split(",") if m]
    r = c.post("/api/rooms", {"name": args.name, "title": args.title or args.name, "description": args.desc or "",
                              "kind": args.kind, "members": members})
    out(args, r, f"room #{r['name']} created; members: {', '.join(m['name'] for m in r['members']) or '-'}")


def cmd_join(args):
    c = client_for(args)
    r = c.post(f"/api/rooms/{q(args.room.lstrip('#'))}/join", {"notify": args.notify, "agent": args.agent})
    out(args, r, f"ok: member of #{r['name']}")


def cmd_leave(args):
    c = client_for(args)
    r = c.post(f"/api/rooms/{q(args.room.lstrip('#'))}/leave", {"agent": args.agent})
    out(args, r, f"ok: saiu de #{r['name']}")


FORMAT_HINT = ("hint: long message on a single line. The UI renders Markdown: use one line per item, "
               "lists and ``` blocks, and send it through stdin: at say -r <room> - <<'EOF' ... EOF "
               "(\\n inside quotes is not a line break).")


def needs_format_hint(body):
    return len(body) > 280 and "\n" not in body


def cmd_say(args):
    c = client_for(args)
    body = read_body(args.text)
    m = c.post(f"/api/rooms/{q(args.room.lstrip('#'))}/messages", {"body": body, "reply_to": args.reply})
    out(args, m, f"sent #{m['id']} in #{m['room']}" +
        (f" (mentions: {', '.join(m['mentions'])})" if m["mentions"] else ""))
    if needs_format_hint(body):
        print(FORMAT_HINT, file=sys.stderr)


def cmd_read(args):
    c = client_for(args)
    room = args.room.lstrip("#")
    data = c.get(f"/api/rooms/{q(room)}/messages", limit=args.limit, after=args.after or None,
                 before=args.before or None, mark_read="1" if not args.peek else None)
    out(args, data, "\n".join(fmt_message(m) for m in data) or f"(no messages in #{room})")


def cmd_inbox(args):
    c = client_for(args)
    data = c.get("/api/inbox", unread=None if args.all else "1", limit=args.limit,
                 mark_read=None if args.peek else "1")
    items = data["items"]
    text = "\n".join(fmt_message(m, show_room=True) for m in items) or "(no new messages for you)"
    out(args, data, text)


def cmd_show(args):
    c = client_for(args)
    m = c.get(f"/api/messages/{args.id}")
    out(args, m, fmt_message(m, show_room=True))


def cmd_history(args):
    c = client_for(args)
    since = None
    if args.days:
        from datetime import datetime, timedelta
        since = (datetime.now().astimezone() - timedelta(days=args.days)).isoformat(timespec="seconds")
    data = c.get("/api/messages", **{"with": args.with_, "from": args.from_, "room": args.room and args.room.lstrip("#"),
                                     "q": args.text, "before": args.before, "after": args.after, "since": since,
                                     "limit": args.limit})
    out(args, data, "\n".join(fmt_message(m, show_room=True) for m in data) or "(nada encontrado)")


def cmd_thread(args):
    c = client_for(args)
    data = c.get(f"/api/messages/{args.id}/thread")
    out(args, data, "\n".join(fmt_message(m, show_room=True) for m in data))


def cmd_search(args):
    c = client_for(args)
    data = c.get("/api/search", q=" ".join(args.text), limit=args.limit)
    out(args, data, "\n".join(fmt_message(m, show_room=True) for m in data) or "(nada encontrado)")


def cmd_task_add(args):
    c = client_for(args)
    t = c.post("/api/tasks", {"room": args.room.lstrip("#"), "title": " ".join(args.title), "body": args.body or "",
                              "assignee": args.to, "source_message_id": args.from_msg})
    out(args, t, f"task created: {fmt_task(t)}")


def cmd_task_list(args):
    c = client_for(args)
    assignee = None
    if args.mine:
        assignee = resolve_identity(args.as_) if not args.human else None
    elif args.to:
        assignee = args.to
    data = c.get("/api/tasks", room=args.room.lstrip("#") if args.room else None, assignee=assignee,
                 status=None if args.all else (args.status or "open_any"))
    out(args, data, "\n".join(fmt_task(t) for t in data) or "(no tasks)")


def cmd_task_set(args):
    c = client_for(args)
    body = {}
    if args.status:
        body["status"] = args.status  # the server also accepts doing, cancel and the old Portuguese values
    if args.to is not None:
        body["assignee"] = args.to
    if args.note:
        body["note"] = args.note
    t = c.patch(f"/api/tasks/{args.id}", body)
    out(args, t, f"task updated: {fmt_task(t)}")


def cmd_spawn(args):
    c = client_for(args)
    rooms = [r for r in (args.rooms or "").split(",") if r]
    prompt = args.prompt if args.prompt != "-" else sys.stdin.read()
    s = c.post("/api/spawns", {"folder": os.path.abspath(args.folder), "name": args.name, "prompt": prompt,
                               "rooms": rooms, "permission_mode": args.mode})
    msg = {"pending": "request recorded; waiting for the human's approval in the UI",
           "started": "session started", "error": "failed"}.get(s["status"], s["status"])
    out(args, s, f"subagent #{s['id']} «{s['agent_name']}»: {msg}\n{s['output']}".rstrip())


def cmd_spawns(args):
    c = client_for(args)
    data = c.get("/api/spawns")
    out(args, data, "\n".join(f"#{s['id']} [{s['status']}] {s['agent_name']} in {s['folder']} "
                              f"(by {s['requested_by']}, {short_time(s['created_at'])})" for s in data) or "(none)")


def cmd_spawn_decide(args):
    c = human_client(args)
    s = c.post(f"/api/spawns/{args.id}/{'approve' if args.cmd == 'approve' else 'reject'}")
    out(args, s, f"#{s['id']} {s['status']}\n{s['output']}".rstrip())


def cmd_watch(args):
    from . import watch
    sys.argv = ["at-watch"] + ([args.name] if args.name else [])
    sys.exit(watch.main())


# --------------------------------------------------------------------------- install into a project
def watch_hook_command(name):
    env = f"AT_AGENT={shlex.quote(name)} "
    for var in ("AT_URL", "AT_HOME"):
        if os.environ.get(var):
            env += f"{var}={shlex.quote(os.environ[var])} "
    return (f"{env}{shlex.quote(sys.executable)} "
            f"{shlex.quote(str(REPO / 'bin' / 'at-watch'))} # {WATCH_MARK}")


def _without_ours(entries):
    new = []
    for e in entries:
        hooks = [h for h in e.get("hooks", []) if WATCH_MARK not in h.get("command", "")]
        if hooks:
            new.append({**e, "hooks": hooks})
    return new


def _git_exclude(project, patterns):
    try:
        path = subprocess.run(["git", "-C", str(project), "rev-parse", "--git-path", "info/exclude"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "not a git repository"
    path = Path(path) if os.path.isabs(path) else Path(project) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    cur = path.read_text() if path.exists() else ""
    add = [p for p in patterns if p not in cur.splitlines()]
    if add:
        with open(path, "a") as f:
            f.write(("" if cur.endswith("\n") or not cur else "\n") + "\n".join(add) + "\n")
    return f"{path} ({'added' if add else 'already there'})"


def slug(text):
    s = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-_")
    s = re.sub(r"-{2,}", "-", s)[:40].strip("-_")
    return s if len(s) >= 2 else f"agent-{s or 'x'}"


def pick_name(client, project):
    """Name for a project: the agent already registered for this folder, else the folder name
    (made unique with -2, -3... if another folder uses it)."""
    agents = client.get("/api/agents")
    for a in agents:
        if a["kind"] == "agent" and a["folder"] == str(project):
            return a["name"]
    taken = {a["name"] for a in agents} | {"all", "here", "todos", "sala", "system", "sistema", "admin"}
    base = slug(project.name)
    name, i = base, 2
    while name in taken:
        name = f"{base[:37]}-{i}"
        i += 1
    return name


def cmd_install(args):
    """Wire a project: register the agent, add the hook, write the identity file."""
    project = Path(args.project_dir).resolve() if args.project_dir else project_root()
    cfg_path = project / ".claude" / "settings.local.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    hooks = cfg.setdefault("hooks", {})
    for ev in ("SessionStart", "Stop"):
        hooks[ev] = _without_ours(hooks.get(ev, []))
    ident = project / PROJECT_FILE
    summary = {}
    if args.remove:
        for ev in ("SessionStart", "Stop"):
            if not hooks[ev]:
                del hooks[ev]
        if not hooks:
            del cfg["hooks"]
        if ident.exists() and not args.dry_run:
            ident.unlink()
        summary["hooks"] = "removidos"
    else:
        if not args.name and not args.dry_run:
            args.name = pick_name(human_client(args), project)
        elif not args.name:
            args.name = slug(project.name)
        hook = {"type": "command", "command": watch_hook_command(args.name), "asyncRewake": True,
                "timeout": 604800}
        hooks["SessionStart"].append({"matcher": "*", "hooks": [hook]})
        hooks["Stop"].append({"hooks": [dict(hook)]})
        legacy = [h["command"] for e in hooks.get("Stop", []) for h in e.get("hooks", [])
                  if "agents_watch.py" in h.get("command", "")]
        if legacy:
            summary["warning"] = ("an old agents.txt hook is still active (both run side by side). "
                                  "To remove it: python3 ~/.claude/skills/agents-txt/scripts/setup.py --remove")
        if not args.dry_run:
            c = human_client(args)
            rooms = [r for r in (args.rooms or "").split(",") if r]
            res = c.post("/api/agents", {"name": args.name, "folder": str(project), "description": args.desc or "",
                                         "rooms": rooms, "force": args.force})
            config.save_agent_token(res["agent"]["name"], res["token"])
            ident.parent.mkdir(parents=True, exist_ok=True)
            ident.write_text(json.dumps({"name": res["agent"]["name"], "url": config.server_url()}, indent=2) + "\n")
            summary["agent"] = res["agent"]["handle"]
            summary["rooms"] = res["agent"]["rooms"]
        summary["hook"] = hook["command"]
    if args.dry_run:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        print(json.dumps(summary, indent=2, ensure_ascii=False), file=sys.stderr)
        return
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, cfg_path)
    json.loads(cfg_path.read_text())
    summary["settings"] = f"{cfg_path} (valid JSON)"
    summary["git"] = _git_exclude(project, [".claude/settings.local.json", PROJECT_FILE])
    if not args.remove:
        summary["next step"] = "open /hooks once (or restart the session) so Claude Code loads the hook"
    print(json.dumps(summary, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- parser
def build_parser():
    p = argparse.ArgumentParser(prog="at", description="AgenticTeams: chat between Claude Code agents")
    p.add_argument("--as", dest="as_", metavar="NAME", help="act as this agent (default: $AT_AGENT or the project)")
    p.add_argument("--human", action="store_true", help="act as the human user (admin token)")
    p.add_argument("--json", action="store_true", help="JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("register", help="register an agent (or renew its token)")
    s.add_argument("name")
    s.add_argument("--folder", help="project folder (default: git root of the current directory)")
    s.add_argument("--desc")
    s.add_argument("--rooms", help="extra rooms, comma-separated (the general room is automatic)")
    s.add_argument("--force", action="store_true", help="accept a name already used by another folder")
    s.set_defaults(fn=cmd_register)

    s = sub.add_parser("install", help="register the project's agent and set up the hook (name taken from the folder)")
    s.add_argument("--project-dir", default=None, help="default: git root of the current directory")
    s.add_argument("--name", help="default: the one already registered for the folder, or the folder name")
    s.add_argument("--desc")
    s.add_argument("--rooms")
    s.add_argument("--force", action="store_true")
    s.add_argument("--remove", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="mostra o settings.local.json resultante, sem gravar")
    s.set_defaults(fn=cmd_install)

    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    s = sub.add_parser("agents", help="lista os participantes")
    s.add_argument("--all", action="store_true", help="inclui autores importados do agents.txt")
    s.set_defaults(fn=cmd_agents)

    s = sub.add_parser("rooms", help="list rooms")
    s.add_argument("--mine", action="store_true")
    s.set_defaults(fn=cmd_rooms)
    s = sub.add_parser("room", help="create a room")
    rs = s.add_subparsers(dest="room_cmd", required=True)
    c = rs.add_parser("create")
    c.add_argument("name")
    c.add_argument("--title")
    c.add_argument("--desc")
    c.add_argument("--kind", default="project", choices=["project", "topic"])
    c.add_argument("--members", help="agents, comma-separated")
    c.set_defaults(fn=cmd_room_create)

    s = sub.add_parser("join", help="join a room (or change the notification mode)")
    s.add_argument("room")
    s.add_argument("--notify", choices=["mentions", "all", "none"])
    s.add_argument("--agent", help="(human) add another agent")
    s.set_defaults(fn=cmd_join)
    s = sub.add_parser("leave")
    s.add_argument("room")
    s.add_argument("--agent")
    s.set_defaults(fn=cmd_leave)

    s = sub.add_parser("say", help="send a message (Markdown; @name mentions, @all wakes the room)")
    s.add_argument("--room", "-r", default="general")
    s.add_argument("--reply", type=int, help="id of the message you are answering")
    s.add_argument("text", nargs="*", help="text; '-' or empty reads stdin (multi-line)")
    s.set_defaults(fn=cmd_say)

    s = sub.add_parser("read", help="room history")
    s.add_argument("room", nargs="?", default="general")
    s.add_argument("--limit", "-n", type=int, default=30)
    s.add_argument("--after", type=int, help="only messages after this id")
    s.add_argument("--before", type=int, help="only messages before this id (the context of a mention)")
    s.add_argument("--peek", action="store_true", help="do not mark as read")
    s.set_defaults(fn=cmd_read)

    s = sub.add_parser("history", help="browse history: conversation with someone, by author, by text...")
    s.add_argument("--with", dest="with_", metavar="NAME", help="conversation between you and NAME, in every room")
    s.add_argument("--from", dest="from_", metavar="NAME", help="only messages by NAME")
    s.add_argument("--room", "-r")
    s.add_argument("--text", "-t", help="contains this text")
    s.add_argument("--before", type=int)
    s.add_argument("--after", type=int)
    s.add_argument("--days", type=float, help="only from the last N days")
    s.add_argument("--limit", "-n", type=int, default=30)
    s.set_defaults(fn=cmd_history)

    s = sub.add_parser("thread", help="reply chain of a message (above and below it)")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_thread)

    s = sub.add_parser("inbox", help="messages for you (unread), and mark them read")
    s.add_argument("--all", action="store_true", help="include read ones")
    s.add_argument("--peek", action="store_true", help="do not mark as read")
    s.add_argument("--limit", "-n", type=int, default=50)
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("show", help="show a message by id")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("search", help="search text in every room")
    s.add_argument("text", nargs="+")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("task", help="tasks")
    ts = s.add_subparsers(dest="task_cmd", required=True)
    c = ts.add_parser("add", help="create a task")
    c.add_argument("title", nargs="+")
    c.add_argument("--room", "-r", default="general")
    c.add_argument("--to", help="assignee")
    c.add_argument("--body")
    c.add_argument("--from-msg", type=int, help="id of the message the task comes from")
    c.set_defaults(fn=cmd_task_add)
    c = ts.add_parser("list", help="list tasks (default: open ones)")
    c.add_argument("--room", "-r")
    c.add_argument("--mine", action="store_true")
    c.add_argument("--to")
    c.add_argument("--status", choices=["open", "in_progress", "done", "cancelled", "open_any"])
    c.add_argument("--all", action="store_true")
    c.set_defaults(fn=cmd_task_list)
    c = ts.add_parser("set", help="change status/assignee: at task set 3 done")
    c.add_argument("id", type=int)
    c.add_argument("status", nargs="?",
                   help="open | in_progress | done | cancelled (shortcuts: doing, cancel)")
    c.add_argument("--to", help="new assignee ('' for none)")
    c.add_argument("--note", help="comment posted in the room")
    c.set_defaults(fn=cmd_task_set)

    s = sub.add_parser("spawn", help="start (or request) a claude --bg subagent in a folder")
    s.add_argument("--folder", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--prompt", required=True, help="the subagent's task ('-' reads stdin)")
    s.add_argument("--rooms")
    s.add_argument("--mode", help="permission mode (see /api/settings)")
    s.set_defaults(fn=cmd_spawn)
    sub.add_parser("spawns", help="list subagent requests").set_defaults(fn=cmd_spawns)
    for name in ("approve", "reject"):
        s = sub.add_parser(name, help=f"(human) {name} a subagent request")
        s.add_argument("id", type=int)
        s.set_defaults(fn=cmd_spawn_decide)

    s = sub.add_parser("watch", help="watcher used by the Claude Code hook")
    s.add_argument("name", nargs="?")
    s.set_defaults(fn=cmd_watch)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except ApiError as e:
        print(f"at: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
