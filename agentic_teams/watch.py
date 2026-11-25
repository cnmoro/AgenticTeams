"""Hook watcher: wakes a Claude Code session when a message *for this agent* arrives.

Configured on SessionStart and Stop with "asyncRewake": true (see `at install`):

    AT_AGENT=sales python3 /mnt/dados1/agentic-teams/bin/at-watch

Same contract as an agents.txt watcher hook (agents_watch.py), but reading the AgenticTeams
inbox instead of a text file:
- long-polls GET /api/inbox?after=<cursor>&wait=50 (the server answers as soon
  as there is a notification: mention, reply, @all, task, human message...);
- keeps the cursor per session in $AT_HOME/watch/<session>.<agent>.cursor;
- first run of a session: unread notifications from the last 3 days wake the
  session at once; otherwise it starts from the newest notification;
- on news, writes them to stderr and exits 2 (that wakes the model); the Stop
  hook of the following turn starts the watcher again from the saved cursor;
- one watcher per session (flock); exits when the Claude Code process is gone;
- if the server is down it keeps retrying quietly.
"""
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timedelta

from . import config
from .client import ApiError, Client, resolve_identity
from .i18n import t

WAIT_S = 50
RETRY_S = 10
MAX_CHARS = 6000
CONTEXT_MESSAGES = 25   # recent messages of the room shown before a notification
CONTEXT_CHARS = 9000
HISTORY_MESSAGES = 15   # past exchanges with whoever called the agent
HISTORY_DAYS = 14
HISTORY_CHARS = 6000
FIRST_RUN_DAYS = 3


def ancestors(pid, levels=3):
    pids = []
    for _ in range(levels):
        try:
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
        if pid <= 1:
            break
        pids.append(pid)
    return pids


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cli_hint(name):
    at = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "at")
    return f"{at} --as {name}"


def roster(client, name, lang="en"):
    """One line per room of this agent with its members, so the agent knows whom it can @mention."""
    try:
        rooms = client.get("/api/rooms", mine="1")
    except ApiError:
        return ""
    lines = [f"  #{r['name']}: " + ", ".join(f"@{m['name']}" for m in r["members"] if m["name"] != name)
             for r in rooms if r["kind"] != "legacy"]
    return t(lang, "roster_header") + "\n" + "\n".join(lines) if lines else ""


def context(client, name, items, lang="en"):
    """What was said in the room right before the notification, so a request like "see what we just
    discussed and apply it" can be acted on at once. Starts after the agent's own last message there
    (it already knows what came before) and keeps the newest messages when it is too long."""
    from .cli import fmt_message
    at = cli_hint(name)
    seen = {m["id"] for m in items}
    shown = set()
    blocks = []
    for room in sorted({m["room"] for m in items})[:3]:
        first = min(m["id"] for m in items if m["room"] == room)
        try:
            msgs = client.get(f"/api/rooms/{room}/messages", before=first, limit=CONTEXT_MESSAGES)
        except ApiError:
            continue
        mine = [i for i, m in enumerate(msgs) if m["author"] == name]
        if mine:
            msgs = msgs[mine[-1]:]  # from my own last message on
        ids = {m["id"] for m in msgs}
        for m in items:  # a replied-to message older than the window
            if m["room"] == room and m.get("reply_to") and m["reply_to"] not in ids | seen:
                try:
                    msgs.insert(0, client.get(f"/api/messages/{m['reply_to']}"))
                except ApiError:
                    pass
        msgs = [m for m in msgs if m["id"] not in seen and m["kind"] != "legacy"]
        if not msgs:
            continue
        lines = [fmt_message(m, lang=lang) for m in msgs]
        dropped = 0
        while len(lines) > 1 and sum(len(l) + 1 for l in lines) > CONTEXT_CHARS // max(1, len(blocks) + 1):
            lines.pop(0)
            dropped += 1
        shown |= {m["id"] for m in msgs[dropped:]}
        more = t(lang, "ctx_more", n=dropped, at=at, room=room, first=first) if dropped else ""
        blocks.append(t(lang, "ctx_header", room=room, more=more) + "\n" + "\n".join(lines))
    return "\n\n".join(blocks), shown


def history(client, name, items, shown, lang="en"):
    """Past exchanges between this agent and whoever called it (any room, last days), and the agent's
    open tasks: what a person would scroll up to see before answering "was what I asked done?"."""
    from datetime import datetime, timedelta
    from .cli import fmt_message, fmt_task
    at = cli_hint(name)
    skip = shown | {m["id"] for m in items}
    since = (datetime.now().astimezone() - timedelta(days=HISTORY_DAYS)).isoformat(timespec="seconds")
    first = min(m["id"] for m in items)
    blocks = []
    for who in list(dict.fromkeys(m["author"] for m in items if m["author_kind"] in ("agent", "human")))[:2]:
        try:
            msgs = client.get("/api/messages", **{"with": who, "since": since, "before": first,
                                                  "limit": HISTORY_MESSAGES})
        except ApiError:
            continue
        msgs = [m for m in msgs if m["id"] not in skip]
        if not msgs:
            continue
        lines = [fmt_message(m, show_room=True, lang=lang) for m in msgs]
        dropped = 0
        while len(lines) > 1 and sum(len(l) + 1 for l in lines) > HISTORY_CHARS:
            lines.pop(0)
            dropped += 1
        more = t(lang, "hist_more", at=at, who=who, first=first) if dropped else ""
        blocks.append(t(lang, "hist_header", who=who, days=HISTORY_DAYS, more=more) + "\n" + "\n".join(lines))
        skip |= {m["id"] for m in msgs}
    try:
        tasks = client.get("/api/tasks", assignee=name, status="open_any")
    except ApiError:
        tasks = []
    if tasks:
        blocks.append(t(lang, "tasks_header") + "\n" + "\n".join(fmt_task(x) for x in tasks[:15]))
    return "\n\n".join(blocks)


def render(name, items, url, team="", ctx="", lang="en"):
    from .cli import fmt_message
    at = cli_hint(name)
    rooms = sorted({m["room"] for m in items})
    lines = [t(lang, "hook_header", n=len(items), name=name, url=url)]
    body = "\n".join(fmt_message(m, show_room=True, lang=lang) for m in items)
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS] + "\n" + t(lang, "hook_cut", at=at)
    lines.append(body)
    if ctx:
        lines.append("\n" + ctx)
    if team:
        lines.append("\n" + team)
    lines.append("\n" + t(lang, "hook_rules", at=at, room=rooms[0]))
    return "\n".join(lines)


def language(client):
    try:
        return client.get("/api/settings").get("language") or "en"
    except ApiError:
        return "en"


def main():
    name = resolve_identity(sys.argv[1] if len(sys.argv) > 1 else None)
    if not name:
        print("at-watch: missing agent name (AT_AGENT or argument)", file=sys.stderr)
        return 1
    try:
        session = json.load(sys.stdin).get("session_id") or "no-session"
    except (ValueError, OSError):
        session = "no-session"
    state = config.home() / "watch"
    state.mkdir(parents=True, exist_ok=True)
    lock = open(state / f"{session}.{name}.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # a watcher is already running for this session
    cursor_file = state / f"{session}.{name}.cursor"
    client = Client(agent=name)
    if not client.token:
        return 0  # not registered: stay silent, never block the session
    parents = ancestors(os.getpid())

    def save(c):
        cursor_file.write_text(str(c))

    try:
        cursor = int(cursor_file.read_text().strip())
        first = False
    except (OSError, ValueError):
        cursor, first = None, True

    while True:
        if parents and not all(alive(p) for p in parents):
            return 0
        try:
            if first:
                res = client.get("/api/inbox", unread="1", limit=50)
                since = (datetime.now().astimezone() - timedelta(days=FIRST_RUN_DAYS)).isoformat()
                items = [i for i in res["items"] if i["created_at"] >= since]
                cursor = res["cursor"]
                save(cursor)
                first = False
            else:
                res = client.get("/api/inbox", after=cursor, wait=WAIT_S, limit=50, _timeout=WAIT_S + 20)
                items = res["items"]
                if items:
                    cursor = max(i["notification_id"] for i in items)
                    save(cursor)
        except ApiError as e:
            if e.status in (401, 403):
                return 0  # token revoked
            time.sleep(RETRY_S)
            continue
        if items:
            lang = language(client)
            ctx, shown = context(client, name, items, lang)
            hist = history(client, name, items, shown, lang)
            print(render(name, items, client.url, roster(client, name, lang), "\n\n".join(x for x in (ctx, hist) if x),
                         lang), file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main())
