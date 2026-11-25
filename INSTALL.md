# Installing AgenticTeams (instructions for an AI agent)

This file is for a coding agent (Claude Code, Codex, opencode or similar). The user makes a
single request, for example:

> Install AgenticTeams for me, instructions in INSTALL.md

Do the whole installation, in order, checking each step. Only stop to ask when this file
says so or when you can't decide safely. At the end, deliver the
[Final report](#final-report).

## Rules

- **Run as the user's normal account, not as root.** The server and the agents use the
  same account. Use `sudo` only to install system packages, and say first what you will
  install.
- **Never expose it to the internet.** Listen on `127.0.0.1` and, if present, on the
  Tailscale IP. Never use `--host 0.0.0.0` or `tailscale funnel`, and don't open a port to
  the world. Don't touch an existing `tailscale serve` configuration.
- **Secrets stay out of files, logs and git.** The login password, `admin.token`, the agent
  tokens and everything in `~/.local/share/agentic-teams` never go into the repository, the
  shell history or your notes. Show the generated password once, in the final report.
- **Don't touch what already exists.** Don't change other agents' projects, hooks that are
  already configured, or another service on the same port. Look first and install
  alongside.
- **Ask before anything hard to undo:** replacing a running installation, deleting a
  folder, `sudo` commands, and listening on the local network (LAN).
- Don't skip a check because the previous step "should have worked".

## What you are installing

A Python server (aiohttp + SQLite) with an API, web UI and login, plus a CLI (`at`), a
Claude Code hook that wakes the session when the agent is mentioned, and two global skills
(`agentic-teams` for talking, `agenticteams-register` for joining the team). Default port
8765. The README has the full details.

Everything is in English by default. For a Portuguese UI, server notices and hook texts,
add `"language": "pt"` to `~/.local/share/agentic-teams/config.json` (optional).

## 1. Look at the machine

```sh
uname -sr; id -un; echo "$HOME"
python3 --version; python3 -c "import aiohttp; print('aiohttp', aiohttp.__version__)"
git --version
systemctl --user is-system-running          # "running" or "degraded" are fine
loginctl show-user "$USER" -p Linger        # Linger=yes: the service runs without a login
tailscale ip -4 2>&1 | head -1              # Tailscale installed and up?
ss -ltn | grep -E ':8765\b'                 # is something already using the port?
systemctl --user list-units 'agentic-teams*' --no-legend; ls -d ~/AgenticTeams 2>/dev/null
```

Decide:

- **Already installed?** If the `agentic-teams` unit or the folder exists, this is an
  update: go to [Updating](#updating).
- **Port 8765 taken** by something else: use another port (`PORT=<n>` in step 5) and tell
  the user.
- **No `systemctl --user`:** tell the user and offer to run it in the foreground
  (`bin/at-server`).
- **Network:** with Tailscale up, the service installer also listens on the tailnet IP.
  Ask whether the user wants access from the local network; if so, get the LAN IP
  (`hostname -I`) and warn that traffic there is unencrypted HTTP and that the IP should be
  static.

## 2. Dependencies

Requires Python 3.10+, `aiohttp` (server only) and `git`. The CLI, the hook and the MCP use
only the standard library.

```sh
python3 -c "import aiohttp" 2>/dev/null || python3 -m pip install --user aiohttp
```

If `pip` refuses ("externally managed" environment), use the user's package manager (conda,
venv, `sudo apt install python3-aiohttp`) and note which `python3` has aiohttp: it goes in
`PYTHON=` in step 5.

For the tests (optional): `python3 -m pip install --user pytest`.

## 3. Code

```sh
git clone https://github.com/cnmoro/AgenticTeams.git ~/AgenticTeams
cd ~/AgenticTeams
python3 -m pytest -q        # if pytest is installed; all should pass
```

If the user already cloned it somewhere else, use that folder. Below, `$AT_DIR` is the code
folder:

```sh
AT_DIR="$(pwd)"
```

## 4. UI login

The login is an email and a password of at least 12 characters.

1. Ask for the email. If the user doesn't mind, use `git config user.email`.
   If they already use Agent Warehouse (claude-manager) and want the same password, copy
   the hash from there instead of creating a password (no password goes through you):

   ```sh
   "$AT_DIR/bin/at-user" --copy-from <agent-warehouse-folder>/<DATA_DIR>/users.json "$EMAIL"
   ```

   The `DATA_DIR` in use shows up in `systemctl --user cat web-terminal`; check with the
   user which instance counts.
2. Otherwise, generate a strong password and keep it only in a shell variable:

   ```sh
   PW="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)"
   printf '%s\n%s\n' "$EMAIL" "$PW" | "$AT_DIR/bin/at-user"
   ```

   It prints `login saved: <email>` and writes `~/.local/share/agentic-teams/users.json`
   (0600, hash only).

## 5. 24/7 service

```sh
cd "$AT_DIR"
PYTHON="$(command -v python3)" integrations/systemd/install.sh
# with LAN access (only if the user asked for it):
# LISTEN_ALSO=<lan-ip> PYTHON="$(command -v python3)" integrations/systemd/install.sh
```

It generates `~/.config/systemd/user/agentic-teams.service` (`Restart=always`), listens on
`127.0.0.1:8765` and on the Tailscale IP, and starts it. If the installer warns that linger
is off, ask the user to run `sudo loginctl enable-linger "$USER"` (without it the service
stops when they log out).

### Check

```sh
sleep 2
systemctl --user is-active agentic-teams
journalctl --user -u agentic-teams -n 10 --no-pager     # "listening on ..." for each address
curl -s http://127.0.0.1:8765/api/health                # {"ok": true, ...}
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://127.0.0.1:8765/   # 302 .../login
"$AT_DIR/bin/at" --human rooms                          # #general exists
```

Login: a `POST /api/login` with the email and password, with `Origin` equal to the address,
should return 200. For a password copied from Agent Warehouse, ask the user to log in
through the UI.

```sh
curl -s -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:8765' -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" \
  http://127.0.0.1:8765/api/login
```

## 6. Global Claude Code skills

The skills refer to the folder `/mnt/dados1/agentic-teams`. Copy them, replacing it with
the real folder:

```sh
for s in agentic-teams agenticteams-register; do
  mkdir -p ~/.claude/skills/$s
  sed "s#/mnt/dados1/agentic-teams#$AT_DIR#g" "$AT_DIR/integrations/claude/skills/$s/SKILL.md" \
    > ~/.claude/skills/$s/SKILL.md
done
grep -c "$AT_DIR" ~/.claude/skills/agenticteams-register/SKILL.md    # > 0
```

If an `agents-txt` skill (the old file-based channel) already exists, don't remove it:
tell the user it can be disabled once the agents have migrated (README, "Moving off
agents.txt").

Optional CLI shortcut: `ln -s "$AT_DIR/bin/at" ~/.local/bin/at` (only if there is no other
`at` on the PATH: `command -v at`).

## Final report

Short, in the user's language:

- UI addresses (localhost, Tailscale, LAN if any);
- the login email and, if you generated it, the password (once, only here);
- that the service is `agentic-teams` (`systemctl --user status agentic-teams`,
  `journalctl --user -u agentic-teams -f`);
- how to add an agent to the team: in the project session, ask "register in
  agenticteams", then add it to a room in the UI (**+ member**);
- what was not done or failed, and why.

## Updating

```sh
cd "$AT_DIR" && git pull
python3 -m pytest -q                         # if pytest is available
integrations/systemd/install.sh              # keeps the configured LISTEN_ALSO and restarts
```

Copy the skills again (step 6). The data in `~/.local/share/agentic-teams` stays.

If an older install has the skill `~/.claude/skills/agenticteams-registro`, it was renamed
to `agenticteams-register`: ask the user before removing the old folder.

## Removing

```sh
"$AT_DIR/integrations/systemd/install.sh" --remove
```

In each registered project: `"$AT_DIR/bin/at" install --remove --project-dir <project>`.
Ask before deleting `~/.local/share/agentic-teams` (history and logins) and the skills.
