# pipeline-agentica

Um escritório de desenvolvimento autônomo: do card à branch pronta para release, com agentes especialistas,
gates determinísticos e tudo registrado em markdown. Implementação da palestra
*Agentes de IA em Produção* (Ricardo Coelho, PHP Conference Brasil 2026).

```
card.md (a-fazer)
   │
   ▼
analista ─► arquiteto ─► dev ─► revisor ─► gate_stg ─► qa ─► gate_qa ─► pronto-para-release
 analise.md   adr.md    plano.md revisao.md  (código)  testes.md (código)   (merge é humano)
                          ▲  + commit            │       + commit     │
                          └──── volta com o motivo ◄──────────────────┘   até max_voltas → bloqueado + aviso
```

Os agentes são o Claude Code em modo headless (`claude -p`), um processo por etapa, cada um com o próprio
system prompt versionado, as próprias ferramentas e a própria cota. O orquestrador é Python puro (sem
dependências) e só move o card; quem aprova é o gate.

## Começando

Requisitos: Python 3.10+, git e o Claude Code autenticado (`claude` no PATH).

```bash
python3 -m pipeline init                                   # cria vault/ (repositório git, abre no Obsidian)
python3 -m pipeline novo-projeto meuapp --repo ~/projetos/meuapp --prefixo APP
#   preencha vault/projetos/meuapp/{PRD,SPEC,TASKS}.md e os gates no WORKFLOW.md
python3 -m pipeline novo-card --projeto meuapp --titulo "Exportar relatório em CSV"
#   escreva a história e os critérios de aceite; mude o status para a-fazer
python3 -m pipeline run                                    # processa a fila uma vez
python3 -m pipeline run --watch 60                         # ou fica de plantão
python3 -m pipeline status
python3 -m pipeline metricas                               # vault/metricas/painel.md
```

### Vault em repositório próprio

O vault pode morar fora desta pasta (`"vault": "~/vaults/escritorio"` no `config.json`) e ter remoto próprio.
Com `"vault_push": true`, a cada passada da fila o orquestrador faz `git pull --rebase` no vault (traz cards
criados em outra máquina), processa, commita o painel e faz `git push`. Falha de rede ou de upstream vira aviso
no stderr e o orquestrador segue; o que ficou commitado sobe na próxima passada. Configure o upstream uma vez
(`git push -u origin main` no vault) e deixe **uma só máquina** processando a fila.

O orquestrador faz `git add -A` na worktree depois do dev e do QA, então o repositório-alvo precisa de um
`.gitignore` que cubra o que os testes geram (`__pycache__/`, `vendor/`, `node_modules/`, `coverage/`...).

Para intervir num card bloqueado: leia o motivo no `## Registro`, responda em `## Notas` (ou corrija o gate)
e mude o status de volta para `a-fazer`. Ele retoma do checkpoint.

Para avisos por e-mail ou chat, defina `notificar_cmd` no `config.json`. O comando recebe a mensagem
no stdin e `PIPELINE_ASSUNTO`, `PIPELINE_EVENTO` e `PIPELINE_CARD` no ambiente. Exemplo:
`mail -s "$PIPELINE_ASSUNTO" voce@exemplo.com`.

## Da palestra para o código

| Slide | Conceito | Onde está |
|---|---|---|
| 9 | Prefixo estável e higiene do cache | `runner.py` `FLAGS_ENXUTAS` corta o prefixo herdado de ~89 mil para ~1,1 mil tokens; `orquestrador._handoff` deixa IDs e caminhos fora do system prompt |
| 10 | Externalizar em vez de carregar | cada agente começa limpo e recebe **caminhos** de arquivos, não conteúdo; o revisor recebe o `diff.patch` pronto |
| 11 | Teto de tokens em todo nível | `--max-budget-usd` por agente (`papeis.*.orcamento_usd`), `orcamento_card_usd` por card, `max_tentativas` e `max_voltas` |
| 12 | System prompt versionado com eval | `prompts/<papel>/system.md` com `versao`; `python3 -m pipeline eval analista`; versão e hash vão para o trace e o frontmatter de cada artefato |
| 14–15 | Inferência só no raciocínio | percepção (diff, refs), ação de commit e gates são código; os testes escritos uma vez rodam sem token |
| 16 | Ferramenta com schema estrito | `--json-schema` por papel em `papeis.py`; o handoff sai como JSON validado |
| 18 | Timeout, nova tentativa, aprovação humana | `timeout_agente_s` (watchdog), espera exponencial, merge fica com uma pessoa |
| 20 | Especialistas com contexto menor | cada papel tem prompt curto e só as ferramentas dele (`LEITURA` ou `EDICAO`) |
| 24 | Estado persistido na fronteira | `estado.json` (checkpoint atômico) + artefatos MD; retomada sem refazer etapas |
| 27 | Isolamento e segredos | uma worktree git por card; o agente não faz commit; segredos no ambiente; unit systemd em `deploy/` |
| 28 | Contrato da story | card sem critérios de aceite é rejeitado sem chamar agente |
| 30 | PRD, SPEC, TASKS, WORKFLOW | `vault/projetos/<nome>/`; os gates vivem no frontmatter do WORKFLOW.md |
| 33–35 | Gates e evidência | `gates.py`; logs em `evidencias/`; nenhum agente aprova o próprio trabalho |
| 36 | Tudo vira MD versionado | o vault é um repositório git e cada transição é um commit |
| 37 | Registro no card, aviso por e-mail | `## Registro` no card; `notificar_cmd` só no bloqueio e na entrega |
| 38 | Métricas do escritório | `metricas.py`: lead time, custo por card, aprovação de primeira, retrabalho, autocorreção e métricas por versão de prompt |
| 39 | Self-healing em degraus | 1 nova tentativa · 2 watchdog e checkpoint · 3 volta ao dev · 5 teto e aviso humano |

## Estrutura

```
pipeline/
  orquestrador.py   máquina de estados do card (etapas, voltas, bloqueio, checkpoint)
  runner.py         invoca o Claude Code headless com contexto enxuto
  papeis.py         ferramentas, schema e artefato de cada papel
  gates.py          roda os comandos de gate e grava a evidência
  vault.py          cards, projeto, artefatos, estado, trace, avisos, commits
  metricas.py       painel sobre o vault
  evals.py          suíte de eval de prompt
prompts/<papel>/system.md   system prompts versionados
evals/<papel>/*.json        casos de eval
templates/                  card e documentos do projeto
tests/                      testes do orquestrador com runner falso (zero tokens)
```

```bash
python3 -m unittest discover -s tests
```

## Ao mudar um prompt

1. Edite `prompts/<papel>/system.md` e suba `versao`.
2. Rode `python3 -m pipeline eval <papel>`; o relatório vai para `vault/metricas/evals/<papel>-v<N>.md`.
3. Compare tokens, custo e sucesso por versão em `vault/metricas/painel.md`.

## Próximos passos (fora do MVP)

- **Release e NOC** (slides 31 e 40): promover entre ambientes, smoke test, rollback automático, e o NOC
  abrindo `vault/incidentes/` como card.
- **Plane ou GitHub Issues** como origem: um adapter que sincroniza o status do card e posta o `## Registro`
  como comentário; o webhook substitui o `--watch`.
- **Contêiner por card** (slide 27): rodar o agente dentro de um contêiner com a worktree montada e
  credencial de escopo mínimo, em vez de direto na máquina.
- **Paralelismo**: vários cards ao mesmo tempo (cada um já tem a própria worktree).
