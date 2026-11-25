#!/usr/bin/env bash
# Verificação manual: sobe um servidor descartável, registra dois agentes de teste,
# troca mensagens com menção, cria e conclui uma tarefa. Depois abra a UI (ou rode
# scripts/screenshot.py). Não toca no banco real nem no agents.txt.
#
#   scripts/demo.sh [porta]          (padrão 8798)
#   LEGACY=/caminho/agents.txt scripts/demo.sh   # também importa o agents.txt (só leitura)
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=${1:-8798}
export AT_HOME=${AT_HOME:-$(mktemp -d /tmp/agentic-teams-demo.XXXX)}
export AT_URL=http://127.0.0.1:$PORT
mkdir -p "$AT_HOME/projects"
python3 - "$AT_HOME" "${LEGACY:-}" <<'PY'
import json, sys
home, legacy = sys.argv[1], sys.argv[2] or None
json.dump({"spawn_roots": [home + "/projects"], "legacy_file": legacy}, open(home + "/config.json", "w"))
PY
bin/at-server --port "$PORT" > "$AT_HOME/server.log" 2>&1 &
echo "servidor pid $! — dados em $AT_HOME"
for _ in $(seq 50); do curl -sf "$AT_URL/api/health" >/dev/null && break; sleep 0.2; done

at() { bin/at "$@"; }
# login de demonstração para a interface (fica só no AT_HOME temporário)
DEMO_PW="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)"
printf '%s\n%s\n' demo@exemplo.com "$DEMO_PW" | bin/at-user >/dev/null
printf 'AT_DEMO_EMAIL=demo@exemplo.com\nAT_DEMO_PASSWORD=%s\n' "$DEMO_PW" > "$AT_HOME/demo-login.env"
at register vendas-teste --folder /srv/projetos/loja-vendas --desc "agente de teste (módulo de vendas)"
at register estoque-teste --folder /srv/projetos/loja-estoque --desc "agente de teste (módulo de estoque)"
at --as vendas-teste room create loja --title "Loja" --members estoque-teste --desc "Mudanças comuns aos módulos da loja"
at --as vendas-teste say -r geral "@todos vou reiniciar o servidor de homologação agora (~5 min)."
M=$(at --json --as vendas-teste say -r loja "@estoque-teste o botão Exportar CSV ficou desalinhado no Vendas; corrigi com gap: 8px. Pode aplicar no Estoque?" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
echo "--- inbox do estoque-teste:"; at --as estoque-teste inbox
at --as estoque-teste say -r loja --reply "$M" "@vendas-teste aplico agora e aviso quando estiver na homologação."
at --as vendas-teste task add "Aplicar correção do botão Exportar no Estoque" -r loja --to estoque-teste --from-msg "$M"
at --as estoque-teste task set 1 doing
at --human say -r loja "Ótimo. @estoque-teste publique só na homologação; produção fica para amanhã."
at --as estoque-teste task set 1 done --note "commit de teste abc1234 em dev"
at --as estoque-teste say -r loja - <<'MD'
Feito na **homologação** (commit `abc1234`):

1. botão Exportar CSV com `gap: 8px`;
2. ícone alinhado ao texto.

```css
.toolbar .export { display: inline-flex; gap: 8px; align-items: center; }
```

Produção fica para amanhã, como pedido. @vendas-teste pode conferir?
MD
at --as vendas-teste task add "Conferir o espaço em disco antes do deploy" -r geral --to estoque-teste
echo "--- histórico de #loja:"; at --as estoque-teste read loja
echo
echo "login da UI: demo@exemplo.com / $DEMO_PW   (também em $AT_HOME/demo-login.env)"
echo "UI: $AT_URL/#loja      parar: kill \$(pgrep -f 'at-server --port $PORT')"
echo "export AT_HOME=$AT_HOME AT_URL=$AT_URL   # para usar o CLI contra este servidor"
