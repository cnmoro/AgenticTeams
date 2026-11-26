"""SQLite storage and domain rules (mentions, notifications, tasks, spawns).

The server is the only writer. All methods are synchronous; the aiohttp server
calls them from the event loop (local scale: a handful of agents).
"""
import hashlib
import json
import re
import secrets
import sqlite3
import threading
from datetime import datetime

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
MENTION_RE = re.compile(r"(?<![\w@/.])@([a-z0-9][a-z0-9_-]{1,39})", re.IGNORECASE)
ROOM_WIDE = {"all", "here", "todos", "sala"}
RESERVED = ROOM_WIDE | {"system", "sistema", "admin"}
TASK_STATUS = ("open", "in_progress", "done", "cancelled")
NOTIFY_MODES = ("mentions", "all", "none")
ROOM_KINDS = ("general", "project", "topic", "legacy")
# Portuguese values from older versions, still accepted on input (and migrated in the database).
STATUS_ALIASES = {"aberta": "open", "em_andamento": "in_progress", "andamento": "in_progress", "doing": "in_progress",
                  "feita": "done", "cancelada": "cancelled", "cancel": "cancelled", "canceled": "cancelled"}
KIND_ALIASES = {"geral": "general", "projeto": "project", "tema": "topic", "legado": "legacy"}
SPAWN_STATUS_ALIASES = {"pendente": "pending", "aprovado": "approved", "iniciado": "started", "erro": "error",
                        "recusado": "rejected"}
OPEN_ANY = ("open_any", "abertas", "open-any")
# A reply chain between the same two agents wakes up to this many messages in total; after that only @mentions wake.
MAX_REPLY_CHAIN = 6
ACK_RE = re.compile(
    r"^(ok(ay)?|certo|entendido|entendi|obrigad[oa]s?|valeu|vlw|combinado|beleza|blz|perfeito|show|"
    r"[óo]timo|feito|de acordo|recebido|anotado|fechado|tudo certo|sem problemas?|of course|thanks?|"
    r"thank you|got it|noted|👍|✅|🙏)([\s,.!;:-]+(ok|obrigad[oa]|valeu|combinado|entendido|e você|"
    r"de nada|por enquanto|então|pela ajuda|pelo aviso|pela resposta|👍|✅|🙏))*[\s.!]*$", re.IGNORECASE)


def is_ack(body):
    """True for a message that only acknowledges or thanks (no question, no new content)."""
    text = MENTION_RE.sub("", body).strip(" \t\n,.;:!-")
    return bool(text) and len(text) <= 60 and "?" not in text and bool(ACK_RE.match(text))

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'agent',          -- agent | human | legacy | system
    folder TEXT,
    description TEXT NOT NULL DEFAULT '',
    token_hash TEXT,
    spawned_by INTEGER REFERENCES agents(id),
    created_at TEXT NOT NULL,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'project',
    created_by INTEGER REFERENCES agents(id),
    created_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS members (
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    notify TEXT NOT NULL DEFAULT 'mentions',
    joined_at TEXT NOT NULL,
    PRIMARY KEY (room_id, agent_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    author_id INTEGER NOT NULL REFERENCES agents(id),
    body TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'chat',            -- chat | system | legacy
    reply_to INTEGER REFERENCES messages(id),
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_room ON messages(room_id, id);
CREATE TABLE IF NOT EXISTS mentions (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    PRIMARY KEY (message_id, agent_id)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    message_id INTEGER NOT NULL REFERENCES messages(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT
);
CREATE INDEX IF NOT EXISTS notifications_agent ON notifications(agent_id, id);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    assignee_id INTEGER REFERENCES agents(id),
    creator_id INTEGER NOT NULL REFERENCES agents(id),
    source_message_id INTEGER REFERENCES messages(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spawns (
    id INTEGER PRIMARY KEY,
    requested_by INTEGER NOT NULL REFERENCES agents(id),
    agent_name TEXT NOT NULL,
    folder TEXT NOT NULL,
    prompt TEXT NOT NULL,
    rooms TEXT NOT NULL DEFAULT '[]',
    permission_mode TEXT NOT NULL,
    status TEXT NOT NULL,                         -- pending | approved | started | error | rejected
    output TEXT NOT NULL DEFAULT '',
    decided_by INTEGER REFERENCES agents(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Error(Exception):
    """Domain error, mapped to HTTP 4xx by the server."""

    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path, human_name="human", lang="en", general_room="general"):
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.lang = lang
        self.general_name = general_room
        self._migrate()
        h = self.one("SELECT name FROM agents WHERE kind='human' ORDER BY id LIMIT 1")
        self.human_name = h["name"] if h else human_name  # an existing install keeps its human's name
        self._bootstrap()

    # ------------------------------------------------------------------ helpers
    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchone()

    def x(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    def tx(self):
        store = self

        class _Tx:
            def __enter__(self):
                store.lock.acquire()
                store.db.execute("BEGIN IMMEDIATE")

            def __exit__(self, et, ev, tb):
                try:
                    store.db.execute("ROLLBACK" if et else "COMMIT")
                finally:
                    store.lock.release()

        return _Tx()

    def get_meta(self, key, default=None):
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else default

    def set_meta(self, key, value):
        self.x("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, str(value)))

    def _migrate(self):
        """Older versions stored Portuguese values; move them to the English ones (idempotent)."""
        with self.lock:
            for old, new in KIND_ALIASES.items():
                self.db.execute("UPDATE rooms SET kind=? WHERE kind=?", (new, old))
            for old, new in STATUS_ALIASES.items():
                self.db.execute("UPDATE tasks SET status=? WHERE status=?", (new, old))
            for old, new in SPAWN_STATUS_ALIASES.items():
                self.db.execute("UPDATE spawns SET status=? WHERE status=?", (new, old))
            self.db.execute("UPDATE notifications SET reason='human' WHERE reason='humano'")

    def _bootstrap(self):
        from .i18n import t
        if not self.one("SELECT 1 FROM agents WHERE kind='system'"):
            self.x("INSERT INTO agents(name,kind,description,created_at) VALUES('system','system',?,?)",
                   (t(self.lang, "system_desc"), now()))
        if not self.agent_by_name(self.human_name):
            self.x("INSERT INTO agents(name,kind,description,created_at) VALUES(?,'human',?,?)",
                   (self.human_name, t(self.lang, "human_desc"), now()))
        if not self.one("SELECT 1 FROM rooms WHERE kind='general'"):
            self.create_room(self.general_name, t(self.lang, "general_title"), t(self.lang, "general_desc"),
                             "general", None)

    def system(self):
        return self.one("SELECT * FROM agents WHERE kind='system' ORDER BY id LIMIT 1")

    def general_room(self):
        return self.one("SELECT * FROM rooms WHERE kind='general' ORDER BY id LIMIT 1")

    # ------------------------------------------------------------------ agents
    def agent_by_name(self, name):
        return self.one("SELECT * FROM agents WHERE name=?", (name,))

    def agent_by_token(self, token):
        return self.one("SELECT * FROM agents WHERE token_hash=?", (hash_token(token),))

    def human(self):
        return self.agent_by_name(self.human_name)

    def register_agent(self, name, folder, description="", spawned_by=None, rooms=()):
        """Create (or re-key) an agent. Returns (agent_row, token)."""
        name = (name or "").strip().lower()
        if not NAME_RE.match(name):
            raise Error("invalid name: use 2-40 characters a-z, 0-9, - or _, starting with a letter or digit")
        if name in RESERVED or name == self.human_name:
            raise Error(f"reserved name: {name}")
        for r in rooms:
            self.room(r)
        token = secrets.token_urlsafe(24)
        ex = self.agent_by_name(name)
        if ex and ex["kind"] != "agent":
            raise Error(f"name already used by a participant of kind {ex['kind']}", 409)
        if ex:
            self.x("UPDATE agents SET folder=?, description=COALESCE(NULLIF(?,''),description), token_hash=?, last_seen=? "
                   "WHERE id=?", (folder, description, hash_token(token), now(), ex["id"]))
        else:
            self.x("INSERT INTO agents(name,kind,folder,description,token_hash,spawned_by,created_at,last_seen) "
                   "VALUES(?,?,?,?,?,?,?,?)",
                   (name, "agent", folder, description, hash_token(token), spawned_by, now(), now()))
        agent = self.agent_by_name(name)
        self.join(self.general_room()["id"], agent["id"])
        for r in rooms:
            self.join(self.room(r)["id"], agent["id"])
        return agent, token

    def delete_agent(self, agent_id):
        """Remove an agent that never took part (used when a spawn fails)."""
        with self.tx():
            if self.db.execute("SELECT 1 FROM messages WHERE author_id=? LIMIT 1", (agent_id,)).fetchone():
                raise Error("the agent already has messages; it cannot be removed", 409)
            for table in ("members", "notifications", "mentions"):
                self.db.execute(f"DELETE FROM {table} WHERE agent_id=?", (agent_id,))
            self.db.execute("DELETE FROM agents WHERE id=?", (agent_id,))

    def touch(self, agent_id):
        self.x("UPDATE agents SET last_seen=? WHERE id=?", (now(), agent_id))

    def agent_dict(self, a):
        rooms = [r["name"] for r in self.q(
            "SELECT r.name FROM members m JOIN rooms r ON r.id=m.room_id WHERE m.agent_id=? ORDER BY r.name", (a["id"],))]
        return {
            "id": a["id"], "name": a["name"], "kind": a["kind"], "folder": a["folder"],
            "handle": f"{a['name']}@{a['folder']}" if a["folder"] else a["name"],
            "description": a["description"], "created_at": a["created_at"], "last_seen": a["last_seen"],
            "spawned_by": a["spawned_by"], "rooms": rooms,
        }

    def list_agents(self, include_legacy=True):
        rows = self.q("SELECT * FROM agents WHERE kind != 'system' ORDER BY kind='human' DESC, kind='legacy', name")
        return [self.agent_dict(a) for a in rows if include_legacy or a["kind"] != "legacy"]

    def legacy_agent(self, tag):
        name = tag.strip().lower()
        a = self.agent_by_name(name)
        if a:
            return a
        self.x("INSERT INTO agents(name,kind,description,created_at) VALUES(?,'legacy','Author imported from agents.txt',?)",
               (name, now()))
        return self.agent_by_name(name)

    # ------------------------------------------------------------------ rooms
    def room_by_name(self, name):
        return self.one("SELECT * FROM rooms WHERE name=?", (name,))

    def room(self, name):
        key = (name or "").lstrip("#").lower()
        r = self.room_by_name(key)
        if not r and key in ("general", "geral"):  # the default room, whatever this install named it
            r = self.general_room()
        if not r and key in ("legacy", "legado"):
            r = self.one("SELECT * FROM rooms WHERE kind='legacy' ORDER BY id LIMIT 1")
        if not r:
            raise Error(f"room not found: {name}", 404)
        return r

    def create_room(self, name, title, description, kind, creator_id, members=()):
        name = (name or "").strip().lstrip("#").lower()
        kind = KIND_ALIASES.get(kind, kind)
        if not NAME_RE.match(name):
            raise Error("invalid room name: use 2-40 characters a-z, 0-9, - or _")
        if kind not in ROOM_KINDS:
            raise Error(f"invalid room kind: {kind} (use {', '.join(ROOM_KINDS)})")
        if self.room_by_name(name):
            raise Error(f"room already exists: {name}", 409)
        ids = []
        for m in members:
            a = self.agent_by_name(m.lstrip("@").lower())
            if not a:
                raise Error(f"agent not found: {m}", 404)
            ids.append(a["id"])
        self.x("INSERT INTO rooms(name,title,description,kind,created_by,created_at) VALUES(?,?,?,?,?,?)",
               (name, title or name, description or "", kind, creator_id, now()))
        room = self.room_by_name(name)
        if creator_id:
            creator = self.one("SELECT * FROM agents WHERE id=?", (creator_id,))
            if creator and creator["kind"] == "agent":
                ids.append(creator_id)
        for aid in ids:
            self.join(room["id"], aid)
        return room

    def join(self, room_id, agent_id, notify=None):
        if notify is not None and notify not in NOTIFY_MODES:
            raise Error(f"invalid notify mode: {notify} (use {', '.join(NOTIFY_MODES)})")
        ex = self.one("SELECT * FROM members WHERE room_id=? AND agent_id=?", (room_id, agent_id))
        if ex:
            if notify:
                self.x("UPDATE members SET notify=? WHERE room_id=? AND agent_id=?", (notify, room_id, agent_id))
            return False
        self.x("INSERT INTO members(room_id,agent_id,notify,joined_at) VALUES(?,?,?,?)",
               (room_id, agent_id, notify or "mentions", now()))
        return True

    def leave(self, room_id, agent_id):
        self.x("DELETE FROM members WHERE room_id=? AND agent_id=?", (room_id, agent_id))

    def room_dict(self, r, for_agent=None):
        members = [dict(name=m["name"], kind=m["kind"], notify=m["notify"]) for m in self.q(
            "SELECT a.name, a.kind, m.notify FROM members m JOIN agents a ON a.id=m.agent_id "
            "WHERE m.room_id=? ORDER BY a.name", (r["id"],))]
        last = self.one("SELECT id, created_at FROM messages WHERE room_id=? ORDER BY id DESC LIMIT 1", (r["id"],))
        d = {"id": r["id"], "name": r["name"], "title": r["title"], "description": r["description"],
             "kind": r["kind"], "created_at": r["created_at"], "members": members,
             "last_message_id": last["id"] if last else 0, "last_message_at": last["created_at"] if last else None,
             "open_tasks": self.one("SELECT count(*) c FROM tasks WHERE room_id=? AND status IN ('open','in_progress')",
                                    (r["id"],))["c"]}
        if for_agent is not None:
            d["unread"] = self.one(
                "SELECT count(*) c FROM notifications n JOIN messages m ON m.id=n.message_id "
                "WHERE n.agent_id=? AND m.room_id=? AND n.read_at IS NULL", (for_agent, r["id"]))["c"]
        return d

    def list_rooms(self, for_agent=None, only_member=False):
        if only_member and for_agent:
            rows = self.q("SELECT r.* FROM rooms r JOIN members m ON m.room_id=r.id WHERE m.agent_id=? "
                          "AND r.archived=0 ORDER BY r.kind='general' DESC, r.name", (for_agent,))
        else:
            rows = self.q("SELECT * FROM rooms WHERE archived=0 ORDER BY kind='general' DESC, kind='legacy', name")
        return [self.room_dict(r, for_agent) for r in rows]

    # ------------------------------------------------------------------ messages
    def message_dict(self, m):
        a = self.one("SELECT name, kind, folder FROM agents WHERE id=?", (m["author_id"],))
        r = self.one("SELECT name FROM rooms WHERE id=?", (m["room_id"],))
        mentioned = [x["name"] for x in self.q(
            "SELECT a.name FROM mentions mm JOIN agents a ON a.id=mm.agent_id WHERE mm.message_id=?", (m["id"],))]
        return {"id": m["id"], "room": r["name"], "author": a["name"], "author_kind": a["kind"],
                "author_folder": a["folder"], "body": m["body"], "kind": m["kind"], "reply_to": m["reply_to"],
                "meta": json.loads(m["meta"] or "{}"), "mentions": mentioned, "created_at": m["created_at"]}

    def get_message(self, mid):
        m = self.one("SELECT * FROM messages WHERE id=?", (mid,))
        if not m:
            raise Error(f"message not found: {mid}", 404)
        return m

    def messages(self, room_id, after=0, before=None, limit=100):
        limit = max(1, min(int(limit), 500))
        if before:
            rows = list(reversed(self.q("SELECT * FROM messages WHERE room_id=? AND id<? ORDER BY id DESC LIMIT ?",
                                        (room_id, before, limit))))
        elif after:
            rows = self.q("SELECT * FROM messages WHERE room_id=? AND id>? ORDER BY id LIMIT ?", (room_id, after, limit))
        else:
            rows = list(reversed(self.q("SELECT * FROM messages WHERE room_id=? ORDER BY id DESC LIMIT ?",
                                        (room_id, limit))))
        return [self.message_dict(m) for m in rows]

    def thread(self, mid, limit=100):
        """The reply chain of a message: its ancestors, the message, and every reply below it (by id)."""
        self.get_message(mid)
        ids, cur = [], mid
        while cur and len(ids) < limit:
            ids.append(cur)
            r = self.one("SELECT reply_to FROM messages WHERE id=?", (cur,))
            cur = r["reply_to"] if r else None
        down = self.q("WITH RECURSIVE t(id) AS (SELECT id FROM messages WHERE reply_to=? "
                      "UNION SELECT m.id FROM messages m JOIN t ON m.reply_to=t.id) SELECT id FROM t LIMIT ?",
                      (mid, limit))
        ids = sorted(set(ids) | {r["id"] for r in down})
        return [self.message_dict(self.get_message(i)) for i in ids]

    def query(self, me_id=None, with_id=None, author_id=None, room_id=None, text=None, before=None, after=None,
              since=None, limit=50):
        """Flexible history lookup, newest `limit` matches returned oldest first.

        with_id: the conversation between `me_id` and that participant, in any room: messages by
        them that reached me (mention, reply, @all, human message in my room...) and my messages
        that mention them or reply to them.
        """
        sql, args = "SELECT m.* FROM messages m WHERE m.kind != 'legacy'", []
        if with_id and me_id:
            sql += (" AND ((m.author_id=? AND EXISTS (SELECT 1 FROM notifications n WHERE n.message_id=m.id "
                    "AND n.agent_id=?)) OR (m.author_id=? AND (EXISTS (SELECT 1 FROM mentions x WHERE "
                    "x.message_id=m.id AND x.agent_id=?) OR m.reply_to IN (SELECT id FROM messages WHERE author_id=?))))")
            args += [with_id, me_id, me_id, with_id, with_id]
        if author_id:
            sql += " AND m.author_id=?"
            args.append(author_id)
        if room_id:
            sql += " AND m.room_id=?"
            args.append(room_id)
        if text:
            sql += " AND m.body LIKE ?"
            args.append(f"%{text}%")
        if before:
            sql += " AND m.id<?"
            args.append(int(before))
        if after:
            sql += " AND m.id>?"
            args.append(int(after))
        if since:
            sql += " AND m.created_at>=?"
            args.append(since)
        sql += " ORDER BY m.id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        return [self.message_dict(m) for m in reversed(self.q(sql, args))]

    def search(self, text, limit=50):
        rows = self.q("SELECT * FROM messages WHERE body LIKE ? ORDER BY id DESC LIMIT ?", (f"%{text}%", limit))
        return [self.message_dict(m) for m in rows]

    def post(self, room_id, author, body, kind="chat", reply_to=None, meta=None, notify=True, created_at=None):
        """Insert a message, resolve mentions and create notifications.

        Who is notified (never the author):
        - agents mentioned by name, even outside the room (reason 'mention', or 'cross' if not a member);
        - the author of the message being replied to (reason 'reply'), unless the reply chain between
          the same two agents is already MAX_REPLY_CHAIN long;
        - @all / @here (or @todos / @sala): every member of the room (reason 'room');
        - members with notify='all' (reason 'all');
        - a message from the human without mentions notifies every member (reason 'human').
        Members with notify='none' only get explicit name mentions.
        A message from an agent that only acknowledges ("ok", "obrigado"...) wakes nobody: that is what
        keeps two agents from thanking each other forever.
        Returns (message_dict, [notified agent ids]).
        """
        body = (body or "").strip()
        if not body:
            raise Error("empty message")
        if reply_to:
            self.get_message(reply_to)
        with self.tx():
            cur = self.db.execute(
                "INSERT INTO messages(room_id,author_id,body,kind,reply_to,meta,created_at) VALUES(?,?,?,?,?,?,?)",
                (room_id, author["id"], body, kind, reply_to, json.dumps(meta or {}, ensure_ascii=False),
                 created_at or now()))
            mid = cur.lastrowid
            targets = {}
            if notify:
                names = {n.lower() for n in MENTION_RE.findall(body)}
                members = {r["agent_id"]: r["notify"] for r in self.db.execute(
                    "SELECT agent_id, notify FROM members WHERE room_id=?", (room_id,))}
                for n in sorted(names - ROOM_WIDE):
                    a = self.db.execute("SELECT id, kind FROM agents WHERE name=?", (n,)).fetchone()
                    if a and a["kind"] in ("agent", "human"):
                        self.db.execute("INSERT OR IGNORE INTO mentions(message_id,agent_id) VALUES(?,?)", (mid, a["id"]))
                        targets.setdefault(a["id"], "mention" if a["id"] in members or a["kind"] == "human" else "cross")
                if reply_to:
                    parent = self.db.execute("SELECT m.author_id, a.kind FROM messages m JOIN agents a "
                                             "ON a.id=m.author_id WHERE m.id=?", (reply_to,)).fetchone()
                    if parent and parent["kind"] in ("agent", "human") and \
                            self._reply_chain(reply_to, author["id"], parent["author_id"]) <= MAX_REPLY_CHAIN:
                        targets.setdefault(parent["author_id"], "reply")
                room_wide = bool(names & ROOM_WIDE)
                human_plain = author["kind"] == "human" and not names
                for aid, mode in members.items():
                    if mode == "none":
                        continue
                    if room_wide:
                        targets.setdefault(aid, "room")
                    elif human_plain:
                        targets.setdefault(aid, "human")
                    elif mode == "all":
                        targets.setdefault(aid, "all")
                targets.pop(author["id"], None)
                kinds = {r["id"]: r["kind"] for r in self.db.execute("SELECT id, kind FROM agents")}
                if author["kind"] == "agent" and is_ack(body):
                    targets = {k: v for k, v in targets.items() if kinds.get(k) == "human"}
                targets = {k: v for k, v in targets.items() if kinds.get(k) in ("agent", "human")}
                ts = now()
                for aid, reason in targets.items():
                    self.db.execute("INSERT INTO notifications(agent_id,message_id,reason,created_at) VALUES(?,?,?,?)",
                                    (aid, mid, reason, ts))
        return self.message_dict(self.get_message(mid)), list(targets)

    def _reply_chain(self, mid, a, b):
        """How many messages, going up the reply_to chain from `mid`, were written only by a or b."""
        n = 0
        while mid and n < 50:
            r = self.db.execute("SELECT author_id, reply_to FROM messages WHERE id=?", (mid,)).fetchone()
            if not r or r["author_id"] not in (a, b):
                break
            n += 1
            mid = r["reply_to"]
        return n + 1  # counts the message being posted

    def notify_direct(self, agent_id, message_id, reason):
        self.x("INSERT INTO notifications(agent_id,message_id,reason,created_at) VALUES(?,?,?,?)",
               (agent_id, message_id, reason, now()))

    # ------------------------------------------------------------------ inbox
    def inbox(self, agent_id, after=0, unread_only=False, limit=100):
        sql = ("SELECT n.id nid, n.reason, n.read_at, m.* FROM notifications n JOIN messages m ON m.id=n.message_id "
               "WHERE n.agent_id=? AND n.id>?")
        if unread_only:
            sql += " AND n.read_at IS NULL"
        sql += " ORDER BY n.id LIMIT ?"
        out = []
        for r in self.q(sql, (agent_id, after, limit)):
            d = self.message_dict(r)
            d.update(notification_id=r["nid"], reason=r["reason"], read=r["read_at"] is not None)
            out.append(d)
        return out

    def last_notification_id(self, agent_id):
        r = self.one("SELECT max(id) m FROM notifications WHERE agent_id=?", (agent_id,))
        return r["m"] or 0

    def mark_read(self, agent_id, upto=None, room_id=None):
        sql = "UPDATE notifications SET read_at=? WHERE agent_id=? AND read_at IS NULL"
        args = [now(), agent_id]
        if upto:
            sql += " AND id<=?"
            args.append(upto)
        if room_id:
            sql += " AND message_id IN (SELECT id FROM messages WHERE room_id=?)"
            args.append(room_id)
        return self.x(sql, args).rowcount

    # ------------------------------------------------------------------ tasks
    def task_dict(self, t):
        def nm(i):
            if not i:
                return None
            r = self.one("SELECT name FROM agents WHERE id=?", (i,))
            return r["name"] if r else None
        room = self.one("SELECT name FROM rooms WHERE id=?", (t["room_id"],))
        return {"id": t["id"], "room": room["name"], "title": t["title"], "body": t["body"], "status": t["status"],
                "assignee": nm(t["assignee_id"]), "creator": nm(t["creator_id"]),
                "source_message_id": t["source_message_id"], "created_at": t["created_at"],
                "updated_at": t["updated_at"]}

    def get_task(self, tid):
        t = self.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if not t:
            raise Error(f"task not found: {tid}", 404)
        return t

    def _assignee_id(self, name):
        if not name:
            return None
        a = self.agent_by_name(name.lstrip("@").lower())
        if not a or a["kind"] not in ("agent", "human"):
            raise Error(f"agent not found: {name}", 404)
        return a["id"]

    def create_task(self, room_id, creator, title, body="", assignee=None, source_message_id=None):
        title = (title or "").strip()
        if not title:
            raise Error("a task needs a title")
        if source_message_id:
            self.get_message(source_message_id)
        aid = self._assignee_id(assignee)
        ts = now()
        cur = self.x("INSERT INTO tasks(room_id,title,body,status,assignee_id,creator_id,source_message_id,created_at,"
                     "updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (room_id, title, body or "", "open", aid, creator["id"], source_message_id, ts, ts))
        return self.get_task(cur.lastrowid)

    def update_task(self, tid, status=None, assignee=None, title=None, body=None):
        t = self.get_task(tid)
        if status is not None:
            status = STATUS_ALIASES.get(status, status)
        if status is not None and status not in TASK_STATUS:
            raise Error(f"invalid status: {status} (use {', '.join(TASK_STATUS)})")
        fields, args = [], []
        if status is not None:
            fields.append("status=?")
            args.append(status)
        if assignee is not None:
            fields.append("assignee_id=?")
            args.append(self._assignee_id(assignee) if assignee else None)
        if title is not None:
            fields.append("title=?")
            args.append(title)
        if body is not None:
            fields.append("body=?")
            args.append(body)
        if not fields:
            raise Error("nothing to change")
        fields.append("updated_at=?")
        args.append(now())
        self.x(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", (*args, t["id"]))
        return self.get_task(tid)

    def list_tasks(self, room_id=None, assignee_id=None, status=None):
        sql, args = "SELECT * FROM tasks WHERE 1=1", []
        if room_id:
            sql += " AND room_id=?"
            args.append(room_id)
        if assignee_id:
            sql += " AND assignee_id=?"
            args.append(assignee_id)
        status = STATUS_ALIASES.get(status, status)
        if status in OPEN_ANY:
            sql += " AND status IN ('open','in_progress')"
        elif status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY status='done', status='cancelled', id DESC"
        return [self.task_dict(t) for t in self.q(sql, args)]

    # ------------------------------------------------------------------ spawns
    def spawn_dict(self, s):
        req = self.one("SELECT name FROM agents WHERE id=?", (s["requested_by"],))
        return {"id": s["id"], "requested_by": req["name"] if req else None, "agent_name": s["agent_name"],
                "folder": s["folder"], "prompt": s["prompt"], "rooms": json.loads(s["rooms"]),
                "permission_mode": s["permission_mode"], "status": s["status"], "output": s["output"],
                "created_at": s["created_at"], "updated_at": s["updated_at"]}

    def get_spawn(self, sid):
        s = self.one("SELECT * FROM spawns WHERE id=?", (sid,))
        if not s:
            raise Error(f"subagent request not found: {sid}", 404)
        return s

    def create_spawn(self, requested_by, agent_name, folder, prompt, rooms, mode, status):
        ts = now()
        cur = self.x("INSERT INTO spawns(requested_by,agent_name,folder,prompt,rooms,permission_mode,status,created_at,"
                     "updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (requested_by, agent_name, folder, prompt, json.dumps(rooms), mode, status, ts, ts))
        return self.get_spawn(cur.lastrowid)

    def set_spawn(self, sid, status, output=None, decided_by=None):
        self.x("UPDATE spawns SET status=?, output=COALESCE(?,output), decided_by=COALESCE(?,decided_by), updated_at=? "
               "WHERE id=?", (status, output, decided_by, now(), sid))
        return self.get_spawn(sid)

    def list_spawns(self):
        return [self.spawn_dict(s) for s in self.q("SELECT * FROM spawns ORDER BY id DESC LIMIT 100")]
