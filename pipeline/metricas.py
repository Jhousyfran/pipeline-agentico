"""As métricas do escritório. Cada card grava as próprias métricas; o painel é uma consulta sobre o vault."""
import json
from collections import defaultdict
from datetime import datetime

from .vault import Vault, agora


def _horas(inicio: str, fim: str | None) -> float | None:
    if not fim:
        return None
    return (datetime.fromisoformat(fim) - datetime.fromisoformat(inicio)).total_seconds() / 3600


def por_card(vault: Vault) -> list[dict]:
    linhas = []
    for card in vault.cards():
        estado = vault.ler_estado(card)
        if not estado:
            continue
        gates = estado.get("gates", {})
        linhas.append({
            "card": card.id,
            "status": card.status,
            "etapa": estado["etapa"],
            "lead_time_h": _horas(estado["iniciado"], estado.get("concluido")),
            "custo_usd": estado["custo_usd"],
            "tokens": estado["tokens"],
            "voltas": estado["voltas_total"],
            "de_primeira": sum(1 for g in gates.values() if g["aprovado"] and g["falhas"] == 0),
            "autocorrecao": sum(1 for g in gates.values() if g["aprovado"] and g["falhas"] > 0),
            "gates": len(gates),
        })
    return linhas


def por_versao_de_prompt(vault: Vault) -> list[dict]:
    """Tokens, custo, duração e taxa de sucesso lado a lado, por papel e versão do prompt."""
    grupos = defaultdict(list)
    for card in vault.cards():
        trace = card.dir / "trace.jsonl"
        if trace.exists():
            for linha in trace.read_text().splitlines():
                r = json.loads(linha)
                grupos[(r["papel"], r["versao_prompt"])].append(r)
    linhas = []
    for (papel, versao), rs in sorted(grupos.items()):
        n = len(rs)
        linhas.append({
            "papel": papel, "versao": versao, "chamadas": n,
            "sucesso": sum(r["ok"] for r in rs) / n,
            "tokens_medio": sum(sum(r["tokens"].values()) for r in rs) / n,
            "custo_medio_usd": sum(r["custo_usd"] for r in rs) / n,
            "duracao_media_s": sum(r["duracao_s"] for r in rs) / n,
        })
    return linhas


def painel(vault: Vault) -> str:
    cards, versoes = por_card(vault), por_versao_de_prompt(vault)
    fmt_h = lambda h: "—" if h is None else f"{h:.1f}"
    md = [f"# Painel do escritório\n\nGerado em {agora()}.\n", "## Por card\n",
          "| Card | Status | Etapa | Lead time (h) | Custo (US$) | Tokens | Voltas | Gates de primeira | Autocorreção |",
          "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| [[demandas/{c['card']}/card\\|{c['card']}]] | {c['status']} | {c['etapa']} | {fmt_h(c['lead_time_h'])} "
           f"| {c['custo_usd']:.2f} | {c['tokens']:,} | {c['voltas']} | {c['de_primeira']}/{c['gates']} "
           f"| {c['autocorrecao']} |" for c in cards]
    if cards:
        prontos = [c for c in cards if c["lead_time_h"] is not None]
        total_gates = sum(c["gates"] for c in cards)
        md += ["", f"- Custo médio por card: US$ {sum(c['custo_usd'] for c in cards) / len(cards):.2f}",
               f"- Retrabalho médio: {sum(c['voltas'] for c in cards) / len(cards):.1f} volta(s) por card",
               f"- Aprovação de primeira: {sum(c['de_primeira'] for c in cards)}/{total_gates} gates"]
        if prontos:
            md.append(f"- Lead time médio: {sum(c['lead_time_h'] for c in prontos) / len(prontos):.1f} h")
    md += ["", "## Por versão de prompt\n",
           "| Papel | Versão | Chamadas | Sucesso | Tokens médios | Custo médio (US$) | Duração média (s) |",
           "|---|---|---|---|---|---|---|"]
    md += [f"| {v['papel']} | v{v['versao']} | {v['chamadas']} | {v['sucesso']:.0%} | {v['tokens_medio']:,.0f} "
           f"| {v['custo_medio_usd']:.3f} | {v['duracao_media_s']:.0f} |" for v in versoes]
    return "\n".join(md) + "\n"
