"""Test fixtures: a real server on a random port, in a background thread, with an isolated AT_HOME."""
import asyncio
import json
import os
import socket
import stat
import sys
import threading
import urllib.request
from pathlib import Path

import pytest
from aiohttp import web

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

FAKE_CLAUDE = """#!/bin/sh
# Fake 'claude' for tests: records its argv (one per line) and pretends to start a session.
for a in "$@"; do printf '%s\\n----\\n' "$a"; done > "$(dirname "$0")/argv.txt"
pwd > "$(dirname "$0")/cwd.txt"
echo "bg-session-1234"
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    def __init__(self, home, settings):
        self.home = home
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.settings = settings
        self.ready = threading.Event()

    def start(self):
        from agentic_teams.server import make_app
        self.app = make_app(self.home / "teams.db", settings=self.settings, public_url=self.url)
        self.store = self.app["store"]
        self.admin = self.app["admin_token"]

        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.runner = web.AppRunner(self.app)
            self.loop.run_until_complete(self.runner.setup())
            self.loop.run_until_complete(web.TCPSite(self.runner, "127.0.0.1", self.port).start())
            self.ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert self.ready.wait(10)

    def stop(self):
        fut = asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop)
        try:
            fut.result(10)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)

    def req(self, method, path, body=None, token=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.url + path, data=data, method=method)
        r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            r.add_header(k, v)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def register(self, name, folder="/tmp", rooms=()):
        st, d = self.req("POST", "/api/agents", {"name": name, "folder": folder, "rooms": list(rooms)}, self.admin)
        assert st == 200, d
        return d["token"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    roots = tmp_path / "projects"
    roots.mkdir()
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    fake = bindir / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("AT_HOME", str(home))
    monkeypatch.delenv("AT_AGENT", raising=False)
    monkeypatch.delenv("AT_TOKEN", raising=False)
    srv = Server(home, {"spawn_roots": [str(roots)], "claude_bin": str(fake), "legacy_file": None})
    monkeypatch.setenv("AT_URL", srv.url)
    srv.start()
    srv.tmp = tmp_path
    srv.roots = roots
    srv.fakebin = bindir
    yield srv
    srv.stop()
