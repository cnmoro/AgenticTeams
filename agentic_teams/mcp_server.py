"""Minimal MCP server (stdio, JSON-RPC 2.0, no dependencies) exposing AgenticTeams as tools.

Register in a project (identity comes from AT_AGENT or .claude/agentic-teams.local.json):

    claude mcp add agentic-teams --scope local -e AT_AGENT=sales -- python3 /mnt/dados1/agentic-teams/bin/at-mcp

The CLI (`at`) remains the primary interface; this is an alternative for
sessions that prefer tools over shell commands.
"""
import json
import sys

from . import __version__
from .cli import FORMAT_HINT, fmt_message, fmt_task, needs_format_hint
from .client import ApiError, Client, q, resolve_identity

S = {"type": "string"}
I = {"type": "integer"}

TOOLS = [
    ("at_inbox", "Messages for you (mentions, replies, @all, tasks, the human). Marks them read.",
     {"all": {"type": "boolean", "description": "include read ones"}}, []),
    ("at_read", "Recent history of a room.", {"room": S, "limit": I, "after": I}, ["room"]),
    ("at_say", "Send a message to a room, in Markdown with line breaks (one line per item). "
               "Use @name to mention (wakes only that agent) and @all for the whole room.",
     {"room": S, "body": S, "reply_to": I}, ["room", "body"]),
    ("at_rooms", "List rooms and their members.", {}, []),
    ("at_agents", "List the team's agents, with folder and last sign of life.", {}, []),
    ("at_join", "Join a room or change the notification mode (mentions | all | none).",
     {"room": S, "notify": S}, ["room"]),
    ("at_search", "Search text in every room.", {"text": S}, ["text"]),
    ("at_task_create", "Create a task in a room, optionally assigned and linked to the message it comes from.",
     {"room": S, "title": S, "body": S, "assignee": S, "source_message_id": I}, ["title"]),
    ("at_task_list", "List tasks (default: open ones).",
     {"room": S, "assignee": S, "status": {"type": "string", "enum": ["open_any", "open", "in_progress", "done", "cancelled"]}}, []),
    ("at_task_update", "Change a task's status (open, in_progress, done, cancelled) or assignee, or comment on it.",
     {"id": I, "status": S, "assignee": S, "note": S}, ["id"]),
    ("at_spawn", "Request (or start, if allowed) a claude --bg subagent in a folder.",
     {"folder": S, "name": S, "prompt": S, "rooms": {"type": "array", "items": S}, "permission_mode": S},
     ["folder", "name", "prompt"]),
]


def call(client, name, a):
    if name == "at_inbox":
        d = client.get("/api/inbox", unread=None if a.get("all") else "1", mark_read="1")
        return "\n".join(fmt_message(m, True) for m in d["items"]) or "(no new messages)"
    if name == "at_read":
        d = client.get(f"/api/rooms/{q(a['room'].lstrip('#'))}/messages", limit=a.get("limit", 30),
                       after=a.get("after"), mark_read="1")
        return "\n".join(fmt_message(m) for m in d) or "(empty room)"
    if name == "at_say":
        m = client.post(f"/api/rooms/{q(a['room'].lstrip('#'))}/messages",
                        {"body": a["body"], "reply_to": a.get("reply_to")})
        return (f"sent #{m['id']} in #{m['room']}" + (f" (mentions: {', '.join(m['mentions'])})" if m["mentions"] else "")
                + (f"\n{FORMAT_HINT}" if needs_format_hint(a["body"]) else ""))
    if name == "at_rooms":
        return "\n".join(f"#{r['name']} [{r['kind']}] {r['title']} — membros: "
                         f"{', '.join(m['name'] for m in r['members'])}" for r in client.get("/api/rooms"))
    if name == "at_agents":
        return "\n".join(f"{x['handle']} ({x['kind']}) seen {x['last_seen']} rooms: {', '.join(x['rooms'])}"
                         for x in client.get("/api/agents") if x["kind"] != "legacy")
    if name == "at_join":
        r = client.post(f"/api/rooms/{q(a['room'].lstrip('#'))}/join", {"notify": a.get("notify")})
        return f"ok: membro de #{r['name']}"
    if name == "at_search":
        return "\n".join(fmt_message(m, True) for m in client.get("/api/search", q=a["text"])) or "(nada)"
    if name == "at_task_create":
        t = client.post("/api/tasks", {"room": a.get("room", "general"), "title": a["title"], "body": a.get("body", ""),
                                       "assignee": a.get("assignee"), "source_message_id": a.get("source_message_id")})
        return "created: " + fmt_task(t)
    if name == "at_task_list":
        d = client.get("/api/tasks", room=a.get("room"), assignee=a.get("assignee"), status=a.get("status", "open_any"))
        return "\n".join(fmt_task(t) for t in d) or "(none)"
    if name == "at_task_update":
        body = {k: a[k] for k in ("status", "assignee", "note") if k in a}
        return "updated: " + fmt_task(client.patch(f"/api/tasks/{a['id']}", body))
    if name == "at_spawn":
        s = client.post("/api/spawns", {k: a.get(k) for k in ("folder", "name", "prompt", "rooms", "permission_mode")})
        return f"subagente #{s['id']} {s['agent_name']}: {s['status']}\n{s['output']}".rstrip()
    raise ValueError(f"ferramenta desconhecida: {name}")


def handle(client, req):
    method, rid = req.get("method"), req.get("id")
    if method == "initialize":
        ver = (req.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return {"protocolVersion": ver, "capabilities": {"tools": {}},
                "serverInfo": {"name": "agentic-teams", "version": __version__},
                "instructions": "Chat of the agent team (AgenticTeams). Read at_inbox when you start; "
                                "mention with @name; warn in #general before touching shared VPN/servers."}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d,
                           "inputSchema": {"type": "object", "properties": p, "required": r}} for n, d, p, r in TOOLS]}
    if method == "tools/call":
        p = req.get("params") or {}
        try:
            if client is None:
                raise ApiError("identity not configured: set AT_AGENT or run 'at install' in the project")
            return {"content": [{"type": "text", "text": call(client, p.get("name"), p.get("arguments") or {})}]}
        except (ApiError, KeyError, ValueError) as e:
            return {"content": [{"type": "text", "text": f"erro: {e}"}], "isError": True}
    if rid is None:
        return None  # notification
    raise LookupError(method)


def main():
    name = resolve_identity(sys.argv[1] if len(sys.argv) > 1 else None)
    client = Client(agent=name) if name else None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        try:
            result = handle(client, req)
            if req.get("id") is None:
                continue
            resp = {"jsonrpc": "2.0", "id": req["id"], "result": result}
        except LookupError as e:
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": f"unknown method: {e}"}}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
