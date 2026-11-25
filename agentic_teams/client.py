"""Small HTTP client (stdlib only) used by the CLI, the hook and the MCP server."""
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

PROJECT_FILE = ".claude/agentic-teams.local.json"


class ApiError(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


def project_root(start=None) -> Path:
    start = Path(start or os.getcwd()).resolve()
    try:
        out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def find_project_file(start=None):
    p = Path(start or os.getcwd()).resolve()
    for d in [p, *p.parents]:
        f = d / PROJECT_FILE
        if f.is_file():
            try:
                return json.loads(f.read_text())
            except ValueError:
                return None
    return None


def resolve_identity(explicit=None):
    """Agent name for this process: --as, then AT_AGENT, then the project file."""
    if explicit:
        return explicit.lstrip("@").lower()
    if os.environ.get("AT_AGENT"):
        return os.environ["AT_AGENT"].lstrip("@").lower()
    pf = find_project_file()
    if pf and pf.get("name"):
        return pf["name"]
    return None


class Client:
    def __init__(self, url=None, token=None, agent=None, admin=False):
        self.url = (url or config.server_url()).rstrip("/")
        self.agent = agent
        if token:
            self.token = token
        elif admin:
            self.token = config.admin_token()
        elif os.environ.get("AT_TOKEN"):
            self.token = os.environ["AT_TOKEN"]
        elif agent:
            self.token = config.agent_token(agent)
        else:
            self.token = None

    def request(self, method, path, body=None, params=None, timeout=30):
        url = self.url + path
        if params:
            params = {k: v for k, v in params.items() if v is not None and v is not False}
            if params:
                url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error") or str(e)
            except ValueError:
                msg = str(e)
            raise ApiError(msg, e.code) from None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            raise ApiError(f"servidor AgenticTeams indisponível em {self.url} ({e})", 0) from None

    def get(self, path, **params):
        timeout = params.pop("_timeout", 30)
        return self.request("GET", path, params=params, timeout=timeout)

    def post(self, path, body=None):
        return self.request("POST", path, body=body or {})

    def patch(self, path, body):
        return self.request("PATCH", path, body=body)


def q(s):
    return urllib.parse.quote(s, safe="")
