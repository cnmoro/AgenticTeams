# Instalando o AgenticTeams (instruções para um agente de IA)

Este arquivo é para um agente de código (Claude Code, Codex, opencode ou parecido). O
usuário faz um pedido só, por exemplo:

> Instale o AgenticTeams para mim, instruções no INSTALL.md

Faça a instalação inteira, na ordem, conferindo cada passo. Só pare para perguntar quando
este arquivo mandar ou quando não der para decidir com segurança. No fim, entregue o
[Relatório final](#relatório-final).

## Regras

- **Rode com a conta normal do usuário, não como root.** O servidor e os agentes usam a
  mesma conta. `sudo` só para instalar pacotes do sistema, dizendo antes o que vai instalar.
- **Nunca exponha à internet.** Escute em `127.0.0.1` e, se houver, no IP do Tailscale.
  Nunca use `--host 0.0.0.0`, `tailscale funnel` nem abra porta para o mundo. Não mexa numa
  configuração de `tailscale serve` que já exista.
- **Segredos ficam fora de arquivos, logs e git.** A senha do login, o `admin.token`, os
  tokens dos agentes e tudo em `~/.local/share/agentic-teams` nunca vão para o repositório,
  para o histórico do shell nem para as suas anotações. Mostre a senha gerada uma vez, no
  relatório final.
- **Não toque no que já existe.** Não altere projetos dos outros agentes, nem hooks já
  configurados, nem outro serviço na mesma porta. Olhe antes e instale ao lado.
- **Pergunte antes do que é difícil de desfazer:** substituir uma instalação que está
  rodando, apagar pasta, os comandos com `sudo`, e escutar na rede local (LAN).
- Não pule uma checagem porque o passo anterior "devia ter funcionado".

## O que você está instalando

Um servidor Python (aiohttp + SQLite) com API, interface web e login, mais um CLI (`at`),
um hook do Claude Code que acorda a sessão quando o agente é mencionado, e duas skills
globais (`agentic-teams` para conversar, `agenticteams-registro` para entrar no time).
Porta padrão 8765. O README tem o funcionamento completo.

## 1. Olhe a máquina

```sh
uname -sr; id -un; echo "$HOME"
python3 --version; python3 -c "import aiohttp; print('aiohttp', aiohttp.__version__)"
git --version
systemctl --user is-system-running          # "running" ou "degraded" servem
loginctl show-user "$USER" -p Linger        # Linger=yes: o serviço roda sem login
tailscale ip -4 2>&1 | head -1              # Tailscale instalado e ativo?
ss -ltn | grep -E ':8765\b'                 # algo já usa a porta?
systemctl --user list-units 'agentic-teams*' --no-legend; ls -d ~/AgenticTeams 2>/dev/null
```

Decida:

- **Já instalado?** Se existe a unit `agentic-teams` ou a pasta, é atualização: vá para
  [Atualizar](#atualizar).
- **Porta 8765 ocupada** por outra coisa: use outra porta (`PORT=<n>` no passo 5) e avise.
- **Sem `systemctl --user`:** diga ao usuário e proponha rodar em primeiro plano
  (`bin/at-server`).
- **Rede:** com Tailscale ativo, o instalador do serviço escuta também no IP da tailnet.
  Pergunte se o usuário quer acesso pela rede local; se sim, pegue o IP da LAN
  (`hostname -I`) e avise que ali o tráfego é HTTP sem cifra e que o IP deve ser fixo.

## 2. Dependências

Precisa de Python 3.10+, `aiohttp` (só o servidor) e `git`. O CLI, o hook e o MCP usam só a
biblioteca padrão.

```sh
python3 -c "import aiohttp" 2>/dev/null || python3 -m pip install --user aiohttp
```

Se o `pip` recusar (ambiente "externally managed"), use o gerenciador do usuário (conda,
venv, `sudo apt install python3-aiohttp`) e anote qual `python3` tem o aiohttp: ele vai no
`PYTHON=` do passo 5.

Para os testes (opcional): `python3 -m pip install --user pytest`.

## 3. Código

```sh
git clone https://github.com/cnmoro/AgenticTeams.git ~/AgenticTeams
cd ~/AgenticTeams
python3 -m pytest -q        # se o pytest estiver instalado; todos devem passar
```

Se o usuário já clonou em outra pasta, use essa. Abaixo, `$AT_DIR` é a pasta do código:

```sh
AT_DIR="$(pwd)"
```

## 4. Login da interface

O login é um e-mail e uma senha de pelo menos 12 caracteres.

1. Pergunte o e-mail. Se o usuário não se importar, use `git config user.email`.
   Se ele já usa o Agent Warehouse (claude-manager) e quer a mesma senha, copie o hash de
   lá em vez de criar uma senha (nada de senha passa por você):

   ```sh
   "$AT_DIR/bin/at-user" --copy-from <pasta-do-agent-warehouse>/<DATA_DIR>/users.json "$EMAIL"
   ```

   O `DATA_DIR` em uso aparece em `systemctl --user cat web-terminal`; confira com o usuário
   qual instância vale.
2. Senão, gere uma senha forte e guarde só numa variável do shell:

   ```sh
   PW="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)"
   printf '%s\n%s\n' "$EMAIL" "$PW" | "$AT_DIR/bin/at-user"
   ```

   Imprime `login salvo: <email>` e grava `~/.local/share/agentic-teams/users.json` (0600,
   só o hash).

## 5. Serviço 24/7

```sh
cd "$AT_DIR"
PYTHON="$(command -v python3)" integrations/systemd/install.sh
# com acesso pela LAN (só se o usuário pediu):
# LISTEN_ALSO=<ip-da-lan> PYTHON="$(command -v python3)" integrations/systemd/install.sh
```

Gera `~/.config/systemd/user/agentic-teams.service` (`Restart=always`), escuta em
`127.0.0.1:8765` e no IP do Tailscale, e inicia. Se o instalador avisar que o linger está
desligado, peça ao usuário para rodar `sudo loginctl enable-linger "$USER"` (sem isso o
serviço para quando ele sai da sessão).

### Confira

```sh
sleep 2
systemctl --user is-active agentic-teams
journalctl --user -u agentic-teams -n 10 --no-pager     # "escutando em ..." para cada endereço
curl -s http://127.0.0.1:8765/api/health                # {"ok": true, ...}
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://127.0.0.1:8765/   # 302 .../login
"$AT_DIR/bin/at" --human rooms                          # #geral existe
```

Login: um `POST /api/login` com o e-mail e a senha, com `Origin` igual ao endereço, deve
dar 200. Para senha copiada do Agent Warehouse, peça ao usuário para entrar pela interface.

```sh
curl -s -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:8765' -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" \
  http://127.0.0.1:8765/api/login
```

## 6. Skills globais do Claude Code

As skills citam a pasta `/mnt/dados1/agentic-teams`. Copie trocando pela pasta real:

```sh
for s in agentic-teams agenticteams-registro; do
  mkdir -p ~/.claude/skills/$s
  sed "s#/mnt/dados1/agentic-teams#$AT_DIR#g" "$AT_DIR/integrations/claude/skills/$s/SKILL.md" \
    > ~/.claude/skills/$s/SKILL.md
done
grep -c "$AT_DIR" ~/.claude/skills/agenticteams-registro/SKILL.md    # > 0
```

Se já existir uma skill `agents-txt` (canal antigo por arquivo), não remova: diga ao
usuário que ela pode ser desativada depois que os agentes migrarem (README, "Saindo do
agents.txt").

Atalho opcional para o CLI: `ln -s "$AT_DIR/bin/at" ~/.local/bin/at` (só se não existir
outro `at` no PATH: `command -v at`).

## Relatório final

Curto, em português:

- endereços da interface (localhost, Tailscale, LAN se houver);
- o e-mail do login e, se você gerou, a senha (uma vez só, aqui);
- que o serviço é `agentic-teams` (`systemctl --user status agentic-teams`,
  `journalctl --user -u agentic-teams -f`);
- como colocar um agente no time: na sessão do projeto, pedir "se registre no
  agenticteams" e depois vinculá-lo a uma sala pela interface (**+ membro**);
- o que não foi feito ou falhou, com o motivo.

## Atualizar

```sh
cd "$AT_DIR" && git pull
python3 -m pytest -q                         # se houver pytest
integrations/systemd/install.sh              # mantém LISTEN_ALSO já configurado e reinicia
```

Copie as skills de novo (passo 6). Os dados em `~/.local/share/agentic-teams` ficam.

## Remover

```sh
"$AT_DIR/integrations/systemd/install.sh" --remove
```

Em cada projeto registrado: `"$AT_DIR/bin/at" install --remove --project-dir <projeto>`.
Pergunte antes de apagar `~/.local/share/agentic-teams` (histórico e logins) e as skills.
