"""Starting subagents: a new `claude --bg` session that joins the team.

Security rules (see README, "Subagents and security model"):
- the folder must exist and resolve (symlinks included) inside one of the
  configured `spawn_roots`; with no roots configured, spawning is disabled;
- the permission mode must be in `spawn_permission_modes`
  (bypassPermissions is never accepted, even if listed);
- the command is an argv list, never a shell string;
- the server pre-registers the agent and gives the session the hook through
  `--settings`, so the project's own files are not touched.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .db import Error

REPO = Path(__file__).resolve().parent.parent
FORBIDDEN_MODES = {"bypassPermissions", "dontAsk"}


def validate_folder(folder: str, roots) -> str:
    if not roots:
        raise Error("starting subagents is disabled: configure spawn_roots (see README)", 403)
    if not folder or not os.path.isabs(folder):
        raise Error("give the folder as an absolute path")
    real = os.path.realpath(folder)
    if not os.path.isdir(real):
        raise Error(f"folder does not exist: {folder}")
    for root in roots:
        r = os.path.realpath(os.path.expanduser(root))
        if real == r or real.startswith(r.rstrip("/") + "/"):
            return real
    raise Error(f"folder outside the allowed roots ({', '.join(roots)}): {folder}", 403)


def validate_mode(mode, allowed) -> str:
    allowed = [m for m in allowed if m not in FORBIDDEN_MODES]
    mode = mode or (allowed[0] if allowed else None)
    if not mode or mode not in allowed:
        raise Error(f"permission mode not allowed: {mode} (use {', '.join(allowed)})", 403)
    return mode


def cli_path() -> str:
    return str(REPO / "bin" / "at")


def watch_command(name, url, home) -> str:
    return (f"AT_AGENT={shlex.quote(name)} AT_URL={shlex.quote(url)} AT_HOME={shlex.quote(str(home))} "
            f"{shlex.quote(sys.executable)} {shlex.quote(str(REPO / 'bin' / 'at-watch'))}")


def hook_settings(name, url, home) -> dict:
    hook = {"type": "command", "command": watch_command(name, url, home), "asyncRewake": True, "timeout": 604800}
    return {"hooks": {"SessionStart": [{"matcher": "*", "hooks": [hook]}], "Stop": [{"hooks": [dict(hook)]}]}}


def full_prompt(name, folder, requester, rooms, prompt, lang="en", general="general") -> str:
    from .i18n import t
    first = rooms[0] if rooms else general
    return t(lang, "subagent_prompt", name=name, requester=requester, folder=folder,
             rooms=", ".join(rooms) or general, at=f"{cli_path()} --as {name}", first=first, prompt=prompt)


def build_argv(claude_bin, name, mode, settings, prompt) -> list:
    return [claude_bin, "--bg", "-n", f"at-{name}", "--permission-mode", mode,
            "--settings", json.dumps(settings), prompt]


def run(argv, cwd, timeout=90) -> tuple[bool, str]:
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        return False, f"binary not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return False, f"timed out ({timeout}s) starting the session"
    out = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip()
    return p.returncode == 0, out[-4000:] or f"exit code {p.returncode}"
