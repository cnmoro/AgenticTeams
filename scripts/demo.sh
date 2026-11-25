#!/usr/bin/env bash
# Manual check: starts a throwaway server, registers two test agents, exchanges messages
# with mentions, creates and finishes a task. Then open the UI (or run scripts/screenshot.py).
# It never touches the real database or an agents.txt.
#
#   scripts/demo.sh [port]           (default 8798)
#   LEGACY=/path/agents.txt scripts/demo.sh   # also imports an agents.txt (read-only)
#   DEMO_LANG=pt scripts/demo.sh              # Portuguese UI and server notices
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=${1:-8798}
export AT_HOME=${AT_HOME:-$(mktemp -d /tmp/agentic-teams-demo.XXXX)}
export AT_URL=http://127.0.0.1:$PORT
mkdir -p "$AT_HOME/projects"
python3 - "$AT_HOME" "${LEGACY:-}" <<'PY'
import json, sys
home, legacy = sys.argv[1], sys.argv[2] or None
import os
json.dump({"spawn_roots": [home + "/projects"], "legacy_file": legacy, "language": os.environ.get("DEMO_LANG", "en")},
          open(home + "/config.json", "w"))
PY
bin/at-server --port "$PORT" > "$AT_HOME/server.log" 2>&1 &
echo "server pid $! — data in $AT_HOME"
for _ in $(seq 50); do curl -sf "$AT_URL/api/health" >/dev/null && break; sleep 0.2; done

at() { bin/at "$@"; }
# demo login for the UI (kept only in the temporary AT_HOME)
DEMO_PW="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)"
printf '%s\n%s\n' demo@example.com "$DEMO_PW" | bin/at-user >/dev/null
printf 'AT_DEMO_EMAIL=demo@example.com\nAT_DEMO_PASSWORD=%s\n' "$DEMO_PW" > "$AT_HOME/demo-login.env"
at register sales-test --folder /srv/projects/store-sales --desc "test agent (sales module)"
at register inventory-test --folder /srv/projects/store-inventory --desc "test agent (inventory module)"
at --as sales-test room create store --title "Store" --members inventory-test --desc "Changes shared by the store modules"
at --as sales-test say -r general "@all restarting the staging server now (~5 min)."
M=$(at --json --as sales-test say -r store "@inventory-test the Export CSV button was misaligned in Sales; I fixed it with gap: 8px. Can you apply it to Inventory?" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
echo "--- inventory-test inbox:"; at --as inventory-test inbox
at --as inventory-test say -r store --reply "$M" "@sales-test applying it now; I will tell you when it is on staging."
at --as sales-test task add "Apply the Export button fix to Inventory" -r store --to inventory-test --from-msg "$M"
at --as inventory-test task set 1 doing
at --human say -r store "Great. @inventory-test publish to staging only; production waits until tomorrow."
at --as inventory-test task set 1 done --note "test commit abc1234 on dev"
at --as inventory-test say -r store - <<'MD'
Done on **staging** (commit `abc1234`):

1. Export CSV button with `gap: 8px`;
2. icon aligned with the text.

```css
.toolbar .export { display: inline-flex; gap: 8px; align-items: center; }
```

Production waits until tomorrow, as asked. @sales-test can you check?
MD
at --as sales-test task add "Check disk space before the deploy" -r general --to inventory-test
echo "--- #store history:"; at --as inventory-test read store
echo
echo "UI login: demo@example.com / $DEMO_PW   (also in $AT_HOME/demo-login.env)"
echo "UI: $AT_URL/#store      stop: kill \$(pgrep -f 'at-server --port $PORT')"
echo "export AT_HOME=$AT_HOME AT_URL=$AT_URL   # to use the CLI against this server"
