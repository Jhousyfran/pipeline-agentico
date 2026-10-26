"""Eval de prompt: cada versão roda a suíte de casos antes de ir para a fila.

Um caso é um JSON em evals/<papel>/ com o card de entrada e asserções determinísticas sobre a saída:

    {"descricao": "...", "card": "<markdown do card>",
     "espera": {"campos": {"pronto": true}, "documento_contem": ["Dado", "Então"], "max_custo_usd": 0.3}}
"""
import json
import tempfile
from pathlib import Path

from .papeis import PAPEIS, carregar_prompt
from .runner import Chamada
from .vault import agora


def avaliar(raiz: Path, cfg: dict, runner, papel_nome: str) -> tuple[bool, str]:
    papel = PAPEIS[papel_nome]
    if papel.edita:
        raise ValueError("eval isolado só para papéis de leitura; dev e qa são avaliados pelos gates")
    prompt = carregar_prompt(raiz / "prompts", papel_nome)
    cfg_papel = cfg["papeis"][papel_nome]
    casos = sorted((raiz / "evals" / papel_nome).glob("*.json"))
    linhas, aprovados, custo = [], 0, 0.0

    for caso_path in casos:
        caso = json.loads(caso_path.read_text())
        espera = caso.get("espera", {})
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card.md"
            card.write_text(caso["card"])
            ex = runner.executar(Chamada(
                papel=papel_nome, system=prompt.texto,
                prompt=f"Etapa de avaliação (você é o {papel_nome}).\nLeia antes de agir:\n- {card}\n\n"
                       "Responda somente no schema JSON pedido.",
                modelo=cfg_papel["modelo"], ferramentas=list(papel.ferramentas), permitidas=list(papel.ferramentas),
                modo_permissao="dontAsk", schema=papel.schema, orcamento_usd=cfg_papel["orcamento_usd"], cwd=Path(tmp),
            ))
        custo += ex.custo_usd
        falhas = [] if ex.ok else [f"execução falhou: {ex.erro}"]
        if ex.ok:
            for campo, valor in espera.get("campos", {}).items():
                if ex.saida.get(campo) != valor:
                    falhas.append(f"`{campo}` = {ex.saida.get(campo)!r}, esperado {valor!r}")
            for trecho in espera.get("documento_contem", []):
                if trecho.lower() not in ex.saida["documento"].lower():
                    falhas.append(f"documento sem `{trecho}`")
        if "max_custo_usd" in espera and ex.custo_usd > espera["max_custo_usd"]:
            falhas.append(f"custo US$ {ex.custo_usd:.3f} acima de {espera['max_custo_usd']}")
        aprovados += not falhas
        linhas.append(f"| {caso_path.stem} | {'✅' if not falhas else '❌ ' + '; '.join(falhas)} "
                      f"| {sum(ex.tokens.values()):,} | {ex.custo_usd:.3f} | {ex.duracao_s:.0f} |")

    ok = bool(casos) and aprovados == len(casos)
    relatorio = "\n".join([
        f"# Eval · {papel_nome} v{prompt.versao} ({prompt.sha})\n",
        f"{agora()} · modelo {cfg_papel['modelo']} · {aprovados}/{len(casos)} casos · US$ {custo:.3f}\n",
        "| Caso | Resultado | Tokens | Custo (US$) | Duração (s) |", "|---|---|---|---|---|", *linhas,
    ]) + "\n"
    return ok, relatorio
