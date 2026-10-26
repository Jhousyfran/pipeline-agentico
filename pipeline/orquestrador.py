"""O orquestrador: código determinístico que move o card entre etapas.

O modelo só decide dentro de cada agente. Quem avança, devolve ou bloqueia o card é este código,
e quem aprova é o gate. A cada fronteira o estado vira checkpoint no vault, de onde o card retoma.
"""
import time
from pathlib import Path
from typing import Callable

from . import gates, git
from .papeis import PAPEIS, Papel, carregar_prompt
from .runner import Chamada
from .vault import Card, Vault, agora

ETAPAS = ("analise", "desenho", "codigo", "revisao", "gate_stg", "qa", "gate_qa")
PAPEL_DA_ETAPA = {"analise": "analista", "desenho": "arquiteto", "codigo": "dev", "revisao": "revisor", "qa": "qa"}
STATUS_NA_FILA = ("a-fazer", "em-andamento")
# Abaixo disto não vale abrir uma chamada: o card para antes de estourar.
SALDO_MINIMO_USD = 0.05


class Bloqueio(Exception):
    """O card para e uma pessoa é avisada (degrau 5 do self-healing)."""


class Orquestrador:
    def __init__(self, cfg: dict, raiz: Path, vault: Vault, runner, dormir: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.raiz = raiz
        self.vault = vault
        self.runner = runner
        self.dormir = dormir
        self.dir_worktrees = (raiz / Path(cfg["worktrees"]).expanduser()).resolve()

    def pendentes(self) -> list[Card]:
        return [c for c in self.vault.cards() if c.status in STATUS_NA_FILA]

    def rodar_fila(self) -> int:
        cards = self.pendentes()
        for card in cards:
            self.processar(card)
        return len(cards)

    # ---- ciclo de um card ----------------------------------------------------

    def processar(self, card: Card) -> None:
        if not card.criterios():
            self.vault.mudar_status(card, "rejeitado")
            self.vault.registrar(card, "orquestrador",
                                 "Rejeitado: a story só entra na fila com os critérios de aceite escritos.")
            self.vault.commit(f"{card.id}: rejeitado sem critérios")
            return

        estado = self.vault.ler_estado(card) or self._estado_novo(card)
        if card.status == "a-fazer" and estado["historico"]:
            # Uma pessoa devolveu o card à fila depois de intervir: retoma do checkpoint com o teto zerado.
            estado["voltas"] = 0
            self.vault.registrar(card, "orquestrador", f"Retomado do checkpoint na etapa `{estado['etapa']}`.")
        self.vault.mudar_status(card, "em-andamento")

        try:
            projeto, docs = self._projeto(card)
            wt = self._preparar_worktree(card, projeto, estado)
            while estado["etapa"] != "pronto":
                etapa = estado["etapa"]
                if etapa in PAPEL_DA_ETAPA:
                    proxima = self._etapa_agente(etapa, card, docs, estado, wt)
                else:
                    proxima = self._etapa_gate(etapa, card, projeto, estado, wt)
                estado["etapa"] = proxima
                self.vault.salvar_estado(card, estado)
                self.vault.commit(f"{card.id}: {etapa} → {proxima}")
        except Bloqueio as b:
            self.vault.salvar_estado(card, estado)
            self.vault.mudar_status(card, "bloqueado")
            self.vault.registrar(card, "orquestrador", f"Bloqueado: {b}")
            self.vault.notificar("bloqueado", card, str(b), self.cfg.get("notificar_cmd", ""))
            self.vault.commit(f"{card.id}: bloqueado")
            return

        estado["concluido"] = agora()
        self.vault.salvar_estado(card, estado)
        self.vault.mudar_status(card, "pronto-para-release")
        mensagem = (f"Todos os gates aprovados. Branch `{estado['branch']}` pronta para merge; "
                    f"custo US$ {estado['custo_usd']:.2f}, {estado['voltas_total']} volta(s). "
                    "O merge é irreversível e fica com uma pessoa.")
        self.vault.registrar(card, "orquestrador", mensagem)
        self.vault.notificar("pronto", card, mensagem, self.cfg.get("notificar_cmd", ""))
        self.vault.commit(f"{card.id}: pronto para release")

    # ---- etapas --------------------------------------------------------------

    def _etapa_agente(self, etapa: str, card: Card, docs: list[Path], estado: dict, wt: Path) -> str:
        papel = PAPEIS[PAPEL_DA_ETAPA[etapa]]
        refs = [card.path, *docs, *self._artefatos_anteriores(etapa, card)]

        if papel.nome == "revisor":
            # Percepção determinística: o revisor não precisa de Bash, recebe o diff pronto.
            patch = self.vault.evidencia(card, "diff.patch")
            patch.write_text(git.diff(wt, estado["base_sha"]))
            if not patch.read_text().strip():
                return self._volta(card, estado, etapa, "O dev não deixou nenhuma alteração no código.")
            refs.append(patch)

        saida, prompt, modelo = self._rodar_agente(papel, card, estado, refs, wt)
        self.vault.escrever_artefato(card, papel.artefato, {
            "card": card.id, "agente": papel.nome, "versao_prompt": prompt.versao, "prompt_sha": prompt.sha,
            "modelo": modelo, "volta": estado["voltas"], "custo_card_usd": round(estado["custo_usd"], 4),
            "data": agora(),
        }, saida["documento"])
        self.vault.registrar(card, papel.nome, f"{saida['resumo']} → [[{papel.artefato}]]")

        if papel.nome == "analista" and not saida["pronto"]:
            perguntas = "\n".join(f"  - {p}" for p in saida["perguntas"]) or "  - (ver analise.md)"
            self._historico(estado, etapa, "aguardando-humano")
            raise Bloqueio(f"O analista precisa de respostas antes de seguir. Responda no card e volte o status "
                           f"para `a-fazer`:\n{perguntas}")
        if papel.nome == "revisor" and not saida["aprovado"]:
            return self._volta(card, estado, etapa, f"A revisão devolveu: {saida['motivo']}")
        if papel.edita:
            sha = git.commit_tudo(wt, f"{card.id} [{papel.nome}] {saida['resumo']}")
            if sha:
                self.vault.registrar(card, "orquestrador", f"Commit `{sha[:10]}` em `{estado['branch']}`.")
        if papel.nome == "dev":
            estado["feedback"] = None
        self._historico(estado, etapa, "ok")
        return self._proxima(etapa)

    def _etapa_gate(self, etapa: str, card: Card, projeto: dict, estado: dict, wt: Path) -> str:
        comando = str(projeto.get(etapa) or "").strip()
        if not comando:
            raise Bloqueio(f"O WORKFLOW.md do projeto não define `{etapa}`. Sem gate não há evidência.")
        resultado = gates.rodar(comando, wt, self.vault.evidencia(card, f"{etapa}.log"), self.cfg["timeout_gate_s"])
        registro = estado["gates"].setdefault(etapa, {"falhas": 0, "aprovado": False})
        if resultado.ok:
            registro["aprovado"] = True
            self._historico(estado, etapa, "aprovado")
            self.vault.registrar(card, "gate", f"`{etapa}` aprovado em {resultado.duracao_s:.0f}s "
                                               f"→ [[evidencias/{resultado.log.name}]]")
            return self._proxima(etapa)
        registro["falhas"] += 1
        return self._volta(card, estado, etapa, f"`{etapa}` reprovou. {resultado.motivo}")

    def _volta(self, card: Card, estado: dict, etapa: str, motivo: str) -> str:
        """Gate ou revisão reprovou: o card volta ao dev com o motivo, até o teto de voltas."""
        estado["voltas"] += 1
        estado["voltas_total"] += 1
        self._historico(estado, etapa, "devolvido", motivo)
        if estado["voltas"] > self.cfg["max_voltas"]:
            raise Bloqueio(f"Teto de {self.cfg['max_voltas']} voltas atingido. Último motivo: {motivo}")
        estado["feedback"] = motivo
        self.vault.registrar(card, "orquestrador", f"Volta {estado['voltas']} para o dev. {motivo.splitlines()[0]}")
        return "codigo"

    # ---- agente ----------------------------------------------------------------

    def _rodar_agente(self, papel: Papel, card: Card, estado: dict, refs: list[Path], wt: Path):
        prompt = carregar_prompt(self.raiz / "prompts", papel.nome)
        cfg_papel = self.cfg["papeis"][papel.nome]
        tentativas = self.cfg["max_tentativas"]
        dirs_extra = sorted({str(r.parent) for r in refs if not r.is_relative_to(wt)})

        for n in range(1, tentativas + 1):
            saldo = self.cfg["orcamento_card_usd"] - estado["custo_usd"]
            if saldo < SALDO_MINIMO_USD:
                raise Bloqueio(f"Orçamento do card esgotado: US$ {estado['custo_usd']:.2f} "
                               f"de US$ {self.cfg['orcamento_card_usd']:.2f}.")
            chamada = Chamada(
                papel=papel.nome,
                system=prompt.texto,
                prompt=self._handoff(papel, card, estado, refs),
                modelo=cfg_papel["modelo"],
                ferramentas=list(papel.ferramentas),
                permitidas=[t for t in papel.ferramentas if t != "Bash"] + list(cfg_papel.get("bash_permitido", [])),
                modo_permissao="acceptEdits" if papel.edita else "dontAsk",
                schema=papel.schema,
                orcamento_usd=min(cfg_papel["orcamento_usd"], saldo),
                cwd=wt,
                dirs_extra=[Path(d) for d in dirs_extra],
            )
            ex = self.runner.executar(chamada)
            estado["custo_usd"] += ex.custo_usd
            estado["tokens"] += sum(ex.tokens.values())
            estado["chamadas"] += 1
            self.vault.trace(card, {
                "ts": agora(), "card": card.id, "etapa": estado["etapa"], "papel": papel.nome, "tentativa": n,
                "modelo": chamada.modelo, "versao_prompt": prompt.versao, "prompt_sha": prompt.sha,
                "ok": ex.ok, "erro": ex.erro, "custo_usd": ex.custo_usd, "tokens": ex.tokens,
                "turnos": ex.turnos, "duracao_s": round(ex.duracao_s, 1), "negacoes": ex.negacoes,
            })
            if ex.ok:
                return ex.saida, prompt, chamada.modelo
            if ex.orcamento_estourado:
                raise Bloqueio(f"O {papel.nome} estourou a cota de US$ {chamada.orcamento_usd:.2f} da etapa.")
            if n < tentativas:
                # Degrau 1 do self-healing: nova tentativa com intervalo crescente.
                self.dormir(self.cfg["espera_base_s"] * 2 ** (n - 1))
        raise Bloqueio(f"O {papel.nome} falhou {tentativas} vezes. Último erro: {ex.erro}")

    def _handoff(self, papel: Papel, card: Card, estado: dict, refs: list[Path]) -> str:
        """Tudo o que é dinâmico (IDs, caminhos, motivo da volta) fica aqui, fora do prefixo cacheado."""
        linhas = [
            f"Card {card.id}: {card.meta.get('titulo', '')}",
            f"Etapa: {estado['etapa']} (você é o {papel.nome}).",
            f"Seu diretório de trabalho é a worktree do repositório, na branch `{estado['branch']}`.",
            "",
            "Leia estes arquivos antes de agir:",
            *[f"- {r}" for r in refs],
        ]
        if papel.nome == "dev" and estado.get("feedback"):
            linhas += ["", f"Esta é a volta {estado['voltas']}. O card voltou com este motivo:", estado["feedback"]]
        linhas += ["", "Responda somente no schema JSON pedido."]
        return "\n".join(linhas)

    # ---- apoio -----------------------------------------------------------------

    def _estado_novo(self, card: Card) -> dict:
        return {
            "card": card.id, "etapa": ETAPAS[0], "branch": f"card/{card.id}",
            "voltas": 0, "voltas_total": 0, "custo_usd": 0.0, "tokens": 0, "chamadas": 0,
            "gates": {}, "historico": [], "feedback": None, "iniciado": agora(), "concluido": None,
        }

    def _projeto(self, card: Card) -> tuple[dict, list[Path]]:
        try:
            return self.vault.projeto(str(card.meta.get("projeto", "")))
        except FileNotFoundError as e:
            raise Bloqueio(str(e)) from e

    def _preparar_worktree(self, card: Card, projeto: dict, estado: dict) -> Path:
        repo = Path(str(projeto.get("repo", ""))).expanduser()
        if not (repo / ".git").exists():
            raise Bloqueio(f"`repo` do WORKFLOW.md não é um repositório git: {repo}")
        if not estado.get("base_sha"):
            estado["base_sha"] = git.rev_parse(repo, str(projeto.get("branch_base", "main")))
        wt = self.dir_worktrees / card.id
        git.preparar_worktree(repo, wt, estado["branch"], estado["base_sha"])
        estado["worktree"] = str(wt)
        return wt

    def _artefatos_anteriores(self, etapa: str, card: Card) -> list[Path]:
        anteriores = [PAPEIS[PAPEL_DA_ETAPA[e]].artefato for e in ETAPAS[:ETAPAS.index(etapa)] if e in PAPEL_DA_ETAPA]
        return [card.dir / a for a in anteriores if (card.dir / a).exists()]

    @staticmethod
    def _proxima(etapa: str) -> str:
        i = ETAPAS.index(etapa)
        return ETAPAS[i + 1] if i + 1 < len(ETAPAS) else "pronto"

    @staticmethod
    def _historico(estado: dict, etapa: str, resultado: str, detalhe: str = "") -> None:
        estado["historico"].append({"ts": agora(), "etapa": etapa, "resultado": resultado,
                                    "detalhe": detalhe[:500]})
