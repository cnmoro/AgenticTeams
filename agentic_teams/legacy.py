"""Read-only bridge from the old /caminho/para/agents.txt into the room "legado".

The file is never written. The import position (byte offset) is kept in the
meta table, so running it again only brings new lines. Lines that do not start
with "[date] TAG:" continue the previous message (multi-line posts).
"""
import os
import re
from datetime import datetime

HEADER = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)(?: ([+-]\d{2}(?::?\d{2})?))?\] ([A-Za-z0-9_-]+)(?: \([^)]*\))?: ?(.*)$")


def parse_ts(ts, tz):
    fmt = "%Y-%m-%d %H:%M:%S" if ts.count(":") == 2 else "%Y-%m-%d %H:%M"
    dt = datetime.strptime(ts, fmt)
    tz = (tz or "-03").replace(":", "")
    if len(tz) == 3:
        tz += "00"
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f"{tz[:3]}:{tz[3:]}"


def parse(text):
    """Yield dicts {tag, created_at, body} from agents.txt content."""
    cur = None
    for line in text.splitlines():
        m = HEADER.match(line)
        if m:
            if cur:
                yield cur
            ts, tz, tag, body = m.groups()
            cur = {"tag": tag, "created_at": parse_ts(ts, tz), "body": body}
        elif line.strip():
            if cur is None:
                cur = {"tag": "desconhecido", "created_at": None, "body": line}
            else:
                cur["body"] += "\n" + line
    if cur:
        yield cur


def import_file(store, path, room_name="legado"):
    """Import new content of `path`. Returns the list of message dicts created."""
    room = store.room_by_name(room_name)
    if not room:
        room = store.create_room(room_name, "Legado (agents.txt)",
                                 f"Importação somente leitura de {path}. Não responda aqui; use as salas novas.",
                                 "legado", None)
    key = f"legacy_offset:{os.path.realpath(path)}"
    offset = int(store.get_meta(key, 0))
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size < offset:  # truncated or recreated
        offset = 0
    if size == offset:
        return []
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read(size - offset)
    cut = chunk.rfind(b"\n") + 1
    if not cut:
        return []
    created = []
    last_tag = store.get_meta(f"{key}:last_tag", "desconhecido")
    for item in parse(chunk[:cut].decode("utf-8", "replace")):
        tag = item["tag"] if item["tag"] != "desconhecido" else last_tag
        author = store.legacy_agent(tag)
        msg, _ = store.post(room["id"], author, item["body"], kind="legacy",
                            meta={"source": os.path.basename(path)}, notify=False,
                            created_at=item["created_at"])
        created.append(msg)
        last_tag = tag
    store.set_meta(key, offset + cut)
    store.set_meta(f"{key}:last_tag", last_tag)
    return created
