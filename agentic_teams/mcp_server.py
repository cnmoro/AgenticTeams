"""Minimal MCP server (stdio, JSON-RPC 2.0, no dependencies) exposing AgenticTeams as tools.

Register in a project (identity comes from AT_AGENT or .claude/agentic-teams.local.json):

    claude mcp add agentic-teams --scope local -e AT_AGENT=vendas -- python3 /mnt/dados1/agentic-teams/bin/at-mcp

The CLI (`at`) remains the primary interface; this is an alternative for
sessions that prefer tools over shell commands.
"""
import json
import sys

from . import __version__
from .cli import fmt_message, fmt_task
from .client import ApiError, Client, q, resolve_identity

S = {"type": "string"}
I = {"type": "integer"}

TOOLS = [
    ("at_inbox", "Mensagens para você (menções, @todos, tarefas, humano). Marca como lidas.",
     {"all": {"type": "boolean", "description": "inclui as já lidas"}}, []),
    ("at_read", "Histórico recente de uma sala.", {"room": S, "limit": I, "after": I}, ["room"]),
    ("at_say", "Envia mensagem numa sala. Use @nome para mencionar (acorda só ele) e @todos para a sala inteira.",
     {"room": S, "body": S, "reply_to": I}, ["room", "body"]),
    ("at_rooms", "Lista as salas e seus membros.", {}, []),
    ("at_agents", "Lista os agentes do time, com pasta e último sinal de vida.", {}, []),
    ("at_join", "Entra numa sala ou muda o modo de notificação (mentions | all | none).",
     {"room": S, "notify": S}, ["room"]),
    ("at_search", "Busca texto em todas as salas.", {"text": S}, ["text"]),
    ("at_task_create", "Cria tarefa numa sala, opcionalmente atribuída e ligada à mensagem de origem.",
     {"room": S, "title": S, "body": S, "assignee": S, "source_message_id": I}, ["title"]),
    ("at_task_list", "Lista tarefas (padrão: abertas).",
     {"room": S, "assignee": S, "status": {"type": "string", "enum": ["abertas", "aberta", "em_andamento", "feita", "cancelada"]}}, []),
    ("at_task_update", "Muda status (aberta, em_andamento, feita, cancelada), responsável ou comenta uma tarefa.",
     {"id": I, "status": S, "assignee": S, "note": S}, ["id"]),
    ("at_spawn", "Pede (ou inicia, se permitido) um subagente claude --bg numa pasta.",
     {"folder": S, "name": S, "prompt": S, "rooms": {"type": "array", "items": S}, "permission_mode": S},
     ["folder", "name", "prompt"]),
]


def call(client, name, a):
    if name == "at_inbox":
        d = client.get("/api/inbox", unread=None if a.get("all") else "1", mark_read="1")
        return "\n".join(fmt_message(m, True) for m in d["items"]) or "(nenhuma mensagem nova)"
    if name == "at_read":
        d = client.get(f"/api/rooms/{q(a['room'].lstrip('#'))}/messages", limit=a.get("limit", 30),
                       after=a.get("after"), mark_read="1")
        return "\n".join(fmt_message(m) for m in d) or "(sala vazia)"
    if name == "at_say":
        m = client.post(f"/api/rooms/{q(a['room'].lstrip('#'))}/messages",
                        {"body": a["body"], "reply_to": a.get("reply_to")})
        return f"enviado #{m['id']} em #{m['room']}" + (f" (menciona: {', '.join(m['mentions'])})" if m["mentions"] else "")
    if name == "at_rooms":
        return "\n".join(f"#{r['name']} [{r['kind']}] {r['title']} — membros: "
                         f"{', '.join(m['name'] for m in r['members'])}" for r in client.get("/api/rooms"))
    if name == "at_agents":
        return "\n".join(f"{x['handle']} ({x['kind']}) visto {x['last_seen']} salas: {', '.join(x['rooms'])}"
                         for x in client.get("/api/agents") if x["kind"] != "legacy")
    if name == "at_join":
        r = client.post(f"/api/rooms/{q(a['room'].lstrip('#'))}/join", {"notify": a.get("notify")})
        return f"ok: membro de #{r['name']}"
    if name == "at_search":
        return "\n".join(fmt_message(m, True) for m in client.get("/api/search", q=a["text"])) or "(nada)"
    if name == "at_task_create":
        t = client.post("/api/tasks", {"room": a.get("room", "geral"), "title": a["title"], "body": a.get("body", ""),
                                       "assignee": a.get("assignee"), "source_message_id": a.get("source_message_id")})
        return "criada: " + fmt_task(t)
    if name == "at_task_list":
        d = client.get("/api/tasks", room=a.get("room"), assignee=a.get("assignee"), status=a.get("status", "abertas"))
        return "\n".join(fmt_task(t) for t in d) or "(nenhuma)"
    if name == "at_task_update":
        body = {k: a[k] for k in ("status", "assignee", "note") if k in a}
        return "atualizada: " + fmt_task(client.patch(f"/api/tasks/{a['id']}", body))
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
                "instructions": "Chat do time de agentes (AgenticTeams). Leia at_inbox ao começar; "
                                "mencione com @nome; avise em #geral antes de mexer em VPN/servidores."}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d,
                           "inputSchema": {"type": "object", "properties": p, "required": r}} for n, d, p, r in TOOLS]}
    if method == "tools/call":
        p = req.get("params") or {}
        try:
            if client is None:
                raise ApiError("identidade não configurada: defina AT_AGENT ou rode 'at install' no projeto")
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
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": f"método desconhecido: {e}"}}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
