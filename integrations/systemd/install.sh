#!/usr/bin/env bash
# Instala o AgenticTeams como serviço de usuário do systemd (24/7, sobe no boot com linger).
# Escuta em 127.0.0.1 e, se houver Tailscale, também no IP da tailnet (não usa serve/funnel,
# então nada fica exposto na internet).
#
#   integrations/systemd/install.sh            # instala/atualiza e (re)inicia
#   integrations/systemd/install.sh --remove   # para e remove
#   PORT=8765 PYTHON=/caminho/python3 integrations/systemd/install.sh
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
UNIT=$HOME/.config/systemd/user/agentic-teams.service
PORT=${PORT:-8765}
PYTHON=${PYTHON:-$(command -v python3)}

if [[ ${1:-} == --remove ]]; then
  systemctl --user disable --now agentic-teams.service 2>/dev/null || true
  rm -f "$UNIT"; systemctl --user daemon-reload
  echo "removido"; exit 0
fi

"$PYTHON" -c "import aiohttp" || { echo "o python $PYTHON não tem aiohttp; use PYTHON=..." >&2; exit 1; }

ARGS="--host 127.0.0.1"
TS_IP="" TS_NAME=""
if command -v tailscale >/dev/null && TS_IP=$(tailscale ip -4 2>/dev/null | head -1) && [[ -n $TS_IP ]]; then
  ARGS+=" --host $TS_IP"
  TS_NAME=$(tailscale status --self --json 2>/dev/null | "$PYTHON" -c \
    "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
  if [[ -n $TS_NAME ]]; then
    ARGS+=" --allow-host $TS_NAME --allow-host ${TS_NAME%%.*}"
  fi
fi

if [[ $(loginctl show-user "$USER" -p Linger --value 2>/dev/null) != yes ]]; then
  echo "AVISO: linger desligado; o serviço só roda com você logado. Ligue com: sudo loginctl enable-linger $USER" >&2
fi

mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<UNIT
# Gerado por $REPO/integrations/systemd/install.sh — rode de novo para atualizar.
[Unit]
Description=AgenticTeams (chat entre agentes Claude Code)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$PYTHON $REPO/bin/at-server $ARGS --port $PORT
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable agentic-teams.service >/dev/null
systemctl --user restart agentic-teams.service
sleep 1.5
systemctl --user --no-pager --lines=0 status agentic-teams.service | head -5
echo
echo "local:     http://127.0.0.1:$PORT"
if [[ -n $TS_IP ]]; then echo "tailscale: http://${TS_NAME:-$TS_IP}:$PORT  (ou http://$TS_IP:$PORT)"; fi
echo "logs:      journalctl --user -u agentic-teams -f"
