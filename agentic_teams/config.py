"""Paths and settings shared by server, CLI and hook.

Everything lives under AT_HOME (default ~/.local/share/agentic-teams):
    teams.db          SQLite database
    admin.token       token of the human user (UI and admin operations), 0600
    agents/<name>.token   token of each registered agent, 0600
    config.json       optional server settings (spawn roots, legacy file...)
"""
import json
import os
import secrets
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8765"


def home() -> Path:
    p = Path(os.environ.get("AT_HOME") or Path.home() / ".local/share/agentic-teams")
    p.mkdir(parents=True, exist_ok=True)
    return p


def server_url() -> str:
    return (os.environ.get("AT_URL") or DEFAULT_URL).rstrip("/")


def _write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")


def read_secret(path: Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def admin_token(create: bool = False) -> str | None:
    path = home() / "admin.token"
    tok = read_secret(path)
    if tok is None and create:
        tok = secrets.token_urlsafe(32)
        _write_secret(path, tok)
    return tok


def agent_token_path(name: str) -> Path:
    return home() / "agents" / f"{name}.token"


def save_agent_token(name: str, token: str) -> None:
    _write_secret(agent_token_path(name), token)


def agent_token(name: str) -> str | None:
    return read_secret(agent_token_path(name))


DEFAULT_SETTINGS = {
    # Folders under which subagents may be started. Empty list = spawning disabled.
    "spawn_roots": [],
    # Binary used to start subagents (tests point this to a fake script).
    "claude_bin": "claude",
    # Permission modes a spawned session may use; the first one is the default.
    "spawn_permission_modes": ["acceptEdits", "plan", "auto", "manual"],
    # If false, agents may only *request* a subagent; the human approves in the UI.
    "agents_can_spawn": False,
    "max_spawns_per_hour": 6,
    # Optional read-only bridge from an old agents.txt into the legacy room.
    "legacy_file": None,
    # Language of the UI, of the notices the server posts and of the hook texts: "en" or "pt".
    "language": "en",
    # Name of the human user (mentioned as @<name>) and of the room created on first start.
    "human_name": "human",
    "general_room": "general",
    "session_ttl_hours": 168,
    "session_idle_hours": 24,
}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    path = home() / "config.json"
    if path.exists():
        s.update(json.loads(path.read_text()))
    return s
