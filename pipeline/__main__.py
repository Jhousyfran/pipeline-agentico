"""CLI: python -m pipeline <comando>"""
import argparse
import fcntl
import json
import re
import sys
import time
from pathlib import Path

from . import frontmatter, metricas
from .git import git
from .orquestrador import Orquestrador
from .runner import ClaudeRunner
from .vault import Vault, agora

RAIZ = Path(__file__).resolve().parent.parent
PASTAS_VAULT = ("projetos", "demandas", "incidentes", "runbooks", "metricas")


def carregar(args) -> tuple[dict, Vault]:
    cfg = json.loads(Path(args.config).read_text())
    return cfg, Vault((RAIZ / Path(cfg["vault"]).expanduser()).resolve())


def _template(arquivo: str, **valores) -> str:
    texto = (RAIZ / "templates" / arquivo).read_text()
    for chave, valor in valores.items():
        texto = texto.replace("{{" + chave + "}}", str(valor))
    return texto


def cmd_init(args) -> None:
    _, vault = carregar(args)
    for pasta in PASTAS_VAULT:
        (vault.raiz / pasta).mkdir(parents=True, exist_ok=True)
    (vault.raiz / "notificacoes.md").touch()
    if not (vault.raiz / ".git").exists():
        git(vault.raiz, "init", "-q")
    vault.commit("vault inicial")
    print(f"vault pronto em {vault.raiz}")


def cmd_novo_projeto(args) -> None:
    _, vault = carregar(args)
    pasta = vault.raiz / "projetos" / args.nome
    if pasta.exists():
        sys.exit(f"projeto já existe: {pasta}")
    pasta.mkdir(parents=True)
    repo = Path(args.repo).expanduser().resolve()
    prefixo = args.prefixo or re.sub(r"[^A-Z]", "", args.nome.upper())[:4] or "CARD"
    for doc in ("PRD.md", "SPEC.md", "TASKS.md", "WORKFLOW.md"):
        (pasta / doc).write_text(_template(f"projeto/{doc}", nome=args.nome, repo=repo, prefixo=prefixo))
    vault.commit(f"projeto {args.nome}")
    print(f"projeto criado em {pasta}\nPreencha PRD, SPEC, TASKS e os gates no WORKFLOW.md antes do primeiro card.")


def cmd_novo_card(args) -> None:
    _, vault = carregar(args)
    meta, _ = vault.projeto(args.projeto)
    prefixo = str(meta.get("prefixo", "CARD"))
    numeros = [int(m.group(1)) for p in (vault.raiz / "demandas").glob(f"{prefixo}-*")
               if (m := re.fullmatch(rf"{re.escape(prefixo)}-(\d+)", p.name))]
    card_id = f"{prefixo}-{max(numeros, default=0) + 1}"
    pasta = vault.raiz / "demandas" / card_id
    pasta.mkdir(parents=True)
    (pasta / "card.md").write_text(_template("card.md", id=card_id, titulo=args.titulo, projeto=args.projeto,
                                             criado=agora()))
    vault.commit(f"{card_id}: card criado")
    print(f"{pasta / 'card.md'}\nEscreva a história e os critérios de aceite; depois mude o status para `a-fazer`.")


def cmd_run(args) -> None:
    cfg, vault = carregar(args)
    trava = open(RAIZ / ".orquestrador.lock", "w")
    try:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("já existe um orquestrador rodando")
    orq = Orquestrador(cfg, RAIZ, vault, ClaudeRunner(cfg["claude_bin"], cfg["timeout_agente_s"]))
    while True:
        if cfg.get("vault_push"):
            _sincronizar(vault, "pull")
        if args.card:
            card = vault.card(args.card)
            if card.status not in ("a-fazer", "em-andamento"):
                sys.exit(f"{card.id} está `{card.status}`; mude para `a-fazer` para entrar na fila")
            orq.processar(card)
            n = 1
        else:
            n = orq.rodar_fila()
        print(f"{agora()} · {n} card(s) processado(s)")
        (vault.raiz / "metricas").mkdir(parents=True, exist_ok=True)
        (vault.raiz / "metricas" / "painel.md").write_text(metricas.painel(vault))
        vault.commit("métricas: painel atualizado")
        if cfg.get("vault_push"):
            _sincronizar(vault, "push")
        if not args.watch:
            return
        time.sleep(args.watch)


def _sincronizar(vault: Vault, direcao: str) -> None:
    erro = vault.sincronizar(direcao)
    if erro:
        print(f"{agora()} · aviso: git {direcao} do vault falhou, sigo e tento na próxima passada\n  {erro}",
              file=sys.stderr)


def cmd_status(args) -> None:
    _, vault = carregar(args)
    for card in vault.cards():
        estado = vault.ler_estado(card) or {}
        print(f"{card.id:<12} {card.status:<20} {estado.get('etapa', '-'):<10} "
              f"US$ {estado.get('custo_usd', 0):>6.2f}  {card.meta.get('titulo', '')}")


def cmd_metricas(args) -> None:
    _, vault = carregar(args)
    painel = metricas.painel(vault)
    (vault.raiz / "metricas").mkdir(parents=True, exist_ok=True)
    (vault.raiz / "metricas" / "painel.md").write_text(painel)
    print(painel)


def cmd_eval(args) -> None:
    from .evals import avaliar
    cfg, vault = carregar(args)
    ok, relatorio = avaliar(RAIZ, cfg, ClaudeRunner(cfg["claude_bin"], cfg["timeout_agente_s"]), args.papel)
    destino = vault.raiz / "metricas" / "evals"
    destino.mkdir(parents=True, exist_ok=True)
    versao = frontmatter.parse((RAIZ / "prompts" / args.papel / "system.md").read_text())[0].get("versao", 0)
    (destino / f"{args.papel}-v{versao}.md").write_text(relatorio)
    print(relatorio)
    sys.exit(0 if ok else 1)


def main() -> None:
    p = argparse.ArgumentParser(prog="pipeline", description="Escritório de desenvolvimento agêntico")
    p.add_argument("--config", default=str(RAIZ / "config.json"))
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("init", help="cria o vault").set_defaults(func=cmd_init)

    s = sub.add_parser("novo-projeto", help="cria PRD/SPEC/TASKS/WORKFLOW de um projeto")
    s.add_argument("nome")
    s.add_argument("--repo", required=True, help="repositório git onde os agentes vão trabalhar")
    s.add_argument("--prefixo", help="prefixo dos cards (ex.: PROJ)")
    s.set_defaults(func=cmd_novo_projeto)

    s = sub.add_parser("novo-card", help="cria um card no backlog")
    s.add_argument("--projeto", required=True)
    s.add_argument("--titulo", required=True)
    s.set_defaults(func=cmd_novo_card)

    s = sub.add_parser("run", help="processa a fila de cards")
    s.add_argument("--card", help="processa só este card")
    s.add_argument("--watch", type=int, metavar="SEG", help="repete a cada SEG segundos")
    s.set_defaults(func=cmd_run)

    sub.add_parser("status", help="lista os cards").set_defaults(func=cmd_status)
    sub.add_parser("metricas", help="gera o painel em vault/metricas/painel.md").set_defaults(func=cmd_metricas)

    s = sub.add_parser("eval", help="roda a suíte de eval de um prompt")
    s.add_argument("papel", choices=["analista", "arquiteto", "revisor"])
    s.set_defaults(func=cmd_eval)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
