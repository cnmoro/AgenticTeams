"""Login for the web UI: email + password, same scheme as claude-manager.

- $AT_HOME/users.json (mode 0600): {"email": {"hash": "scrypt$N$r$p$salt_b64$key_b64", "updated": iso}}
  The hash format is the one claude-manager writes, so its entries can be copied as is.
- Sessions live in memory (a restart logs everyone out), in an HttpOnly SameSite=Strict cookie.
- Login throttling: 5 attempts per IP per 15 min, counted before the check; at most 4 hashes at once.
  No global lockout, so nobody can lock the owner out.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime

from . import config

SCRYPT = {"n": 32768, "r": 8, "p": 1}
MAXMEM = 64 * 1024 * 1024
KEYLEN = 64
COOKIE = "at_session"


def users_file():
    return config.home() / "users.json"


def _scrypt(password, salt, n, r, p):
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=KEYLEN)


def hash_password(password):
    salt = os.urandom(16)
    key = _scrypt(password, salt, **SCRYPT)
    return (f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}$"
            f"{base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}")


def verify_password(password, stored):
    try:
        alg, n, r, p, salt, key = str(stored).split("$")
        if alg != "scrypt":
            return False
        expected = base64.b64decode(key)
        actual = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


def normalize(email):
    return str(email or "").strip().lower()


_cache = {"mtime": None, "users": {}}


def load_users():
    """Read users.json, cached by mtime (changes apply without restart). A damaged file keeps the last good copy."""
    path = users_file()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return {}
    if mtime != _cache["mtime"]:
        try:
            users = json.loads(path.read_text())
            if not isinstance(users, dict):
                raise ValueError("not an object")
            _cache["users"] = users
        except ValueError:
            pass
        _cache["mtime"] = mtime
    return _cache["users"]


def save_users(users):
    path = users_file()
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(users, f, indent=2)
    os.replace(tmp, path)
    _cache["mtime"] = None


def fingerprint(email):
    u = load_users().get(email)
    return hashlib.sha256(u["hash"].encode()).hexdigest()[:32] if u else None


_dummy = None


def check_credentials(email, password):
    """Returns the normalized email, or None. Unknown emails still cost a full hash."""
    global _dummy
    if not isinstance(email, str) or not isinstance(password, str) or not password or len(password) > 1024:
        return None
    key = normalize(email)
    user = load_users().get(key)
    if _dummy is None:
        _dummy = hash_password(secrets.token_hex(16))
    ok = verify_password(password, user["hash"] if user else _dummy)
    return key if ok and user else None


def set_user(email, password):
    users = dict(load_users())
    users[normalize(email)] = {"hash": hash_password(password),
                               "updated": datetime.now().astimezone().isoformat(timespec="seconds")}
    save_users(users)


class Sessions:
    def __init__(self, ttl_s=168 * 3600, idle_s=24 * 3600):
        self.ttl_s, self.idle_s = ttl_s, idle_s
        self.items = {}  # sha256(token) -> {email, fp, created, last}

    @staticmethod
    def _id(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def create(self, email):
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.items[self._id(token)] = {"email": email, "fp": fingerprint(email), "created": now, "last": now}
        return token

    def get(self, token):
        if not token:
            return None
        sid = self._id(token)
        s = self.items.get(sid)
        now = time.time()
        if not s or now - s["created"] > self.ttl_s or now - s["last"] > self.idle_s \
                or fingerprint(s["email"]) != s["fp"]:  # password changed or user removed
            self.items.pop(sid, None)
            return None
        s["last"] = now
        return s

    def destroy(self, token):
        if token:
            self.items.pop(self._id(token), None)


class Limiter:
    def __init__(self, max_per_ip=5, window_s=15 * 60, max_in_flight=4):
        self.max_per_ip, self.window_s, self.max_in_flight = max_per_ip, window_s, max_in_flight
        self.by_ip = {}
        self.in_flight = 0

    def begin(self, ip):
        now = time.time()
        recent = [t for t in self.by_ip.get(ip, []) if now - t < self.window_s]
        if len(recent) >= self.max_per_ip:
            return int(self.window_s - (now - recent[0])) + 1
        if self.in_flight >= self.max_in_flight:
            return 1
        recent.append(now)
        self.by_ip[ip] = recent
        self.in_flight += 1
        return 0

    def end(self, ip, success):
        self.in_flight -= 1
        if success:
            self.by_ip.pop(ip, None)


def main(argv=None):
    """at-user: create/update or remove a login for the web UI.

        at-user                      # asks email and password (or reads two lines from stdin)
        at-user --remove EMAIL
        at-user --copy-from /mnt/dados1/claude-manager/data/users.json EMAIL   # reuse a claude-manager login
    """
    import argparse
    import getpass
    import sys
    p = argparse.ArgumentParser(prog="at-user", description="AgenticTeams web UI logins")
    p.add_argument("--remove", metavar="EMAIL")
    p.add_argument("--copy-from", metavar="USERS_JSON", help="copy the hash from another users.json (e.g. claude-manager's)")
    p.add_argument("email", nargs="?")
    a = p.parse_args(argv)
    users = dict(load_users())
    if a.remove:
        if users.pop(normalize(a.remove), None) is None:
            sys.exit(f"no such login: {a.remove}")
        save_users(users)
        print(f"removed: {normalize(a.remove)}")
        return 0
    if a.copy_from:
        src = json.loads(open(a.copy_from).read())
        key = normalize(a.email)
        if key not in src:
            sys.exit(f"{key} is not in {a.copy_from}")
        users[key] = src[key]
        save_users(users)
        print(f"login {key} copied from {a.copy_from} to {users_file()}")
        return 0
    if sys.stdin.isatty():
        email = a.email or input("Email: ")
        pw = getpass.getpass("Password (min. 12): ")
        if getpass.getpass("Again: ") != pw:
            sys.exit("the passwords do not match")
    else:
        lines = sys.stdin.read().splitlines() + ["", ""]
        email, pw = (a.email, lines[0]) if a.email else (lines[0], lines[1])
    if len(pw) < 12:
        sys.exit("password too short (minimum 12 characters)")
    set_user(email, pw)
    print(f"login saved: {normalize(email)} in {users_file()}")
    return 0
