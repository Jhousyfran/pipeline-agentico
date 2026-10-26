"""Testes do orquestrador com um runner falso: o fluxo inteiro sem gastar um token."""
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import git
from pipeline.runner import Chamada, Execucao
from pipeline.orquestrador import Orquestrador
from pipeline.vault import Vault

RAIZ = Path(__file__).resolve().parent.parent

CARD = """---
id: DEMO-1
titulo: Exportar relatório em CSV
projeto: demo
status: a-fazer
---

## História

Como gestor, quero exportar o relatório em CSV.

## Critérios de aceite

- Dado um relatório filtrado, quando exporto, então o CSV traz só as linhas filtradas

## Registro
"""


class RunnerFalso:
    """Devolve respostas roteirizadas por papel; o dev falso escreve um arquivo para gerar diff."""

    def __init__(self, roteiro: dict | None = None, custo: float = 0.01):
        self.roteiro = {k: list(v) for k, v in (roteiro or {}).items()}
        self.custo = custo
        self.chamadas: list[Chamada] = []

    def executar(self, c: Chamada) -> Execucao:
        self.chamadas.append(c)
        fila = self.roteiro.get(c.papel)
        resposta = fila.pop(0) if fila else {}
        if isinstance(resposta, Execucao):
            return resposta
        if c.papel == "dev":
            (c.cwd / "export.py").write_text(f"# versão {sum(x.papel == 'dev' for x in self.chamadas)}\n")
        saida = {"documento": f"# {c.papel}", "resumo": f"{c.papel} ok", "pronto": True, "perguntas": [],
                 "aprovado": True, "motivo": "", **resposta}
        return Execucao(True, saida=saida, custo_usd=self.custo, tokens={"input_tokens": 100, "output_tokens": 10})

    def papeis(self) -> list[str]:
        return [c.papel for c in self.chamadas]


class TesteOrquestrador(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git.git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("demo\n")
        git.commit_tudo(self.repo, "inicial")

        self.vault = Vault(self.tmp / "vault")
        (self.vault.raiz / "demandas" / "DEMO-1").mkdir(parents=True)
        (self.vault.raiz / "demandas" / "DEMO-1" / "card.md").write_text(CARD)
        self.projeto = self.vault.raiz / "projetos" / "demo"
        self.projeto.mkdir(parents=True)
        self.definir_gates("true", "true")

        self.cfg = json.loads((RAIZ / "config.json").read_text())
        self.cfg.update(worktrees=str(self.tmp / "worktrees"), espera_base_s=0, notificar_cmd="")

    def definir_gates(self, stg: str, qa: str) -> None:
        (self.projeto / "WORKFLOW.md").write_text(
            f"---\nrepo: {self.repo}\nbranch_base: main\ngate_stg: {stg}\ngate_qa: {qa}\n---\n# WORKFLOW\n")

    def rodar(self, runner: RunnerFalso) -> tuple[dict, str]:
        orq = Orquestrador(self.cfg, RAIZ, self.vault, runner, dormir=lambda s: None)
        orq.processar(self.vault.card("DEMO-1"))
        card = self.vault.card("DEMO-1")
        return self.vault.ler_estado(card), card.status

    def test_caminho_feliz_chega_a_pronto_para_release(self):
        runner = RunnerFalso()
        estado, status = self.rodar(runner)
        self.assertEqual(status, "pronto-para-release")
        self.assertEqual(runner.papeis(), ["analista", "arquiteto", "dev", "revisor", "qa"])
        pasta = self.vault.raiz / "demandas" / "DEMO-1"
        for artefato in ("analise.md", "adr.md", "plano.md", "revisao.md", "testes.md",
                         "evidencias/gate_stg.log", "evidencias/gate_qa.log", "evidencias/diff.patch"):
            self.assertTrue((pasta / artefato).exists(), artefato)
        self.assertEqual(len((pasta / "trace.jsonl").read_text().splitlines()), 5)
        self.assertEqual(estado["gates"]["gate_stg"], {"falhas": 0, "aprovado": True})
        self.assertIn("versao_prompt: 1", (pasta / "analise.md").read_text())
        self.assertIn("export.py", git.diff(Path(estado["worktree"]), estado["base_sha"]))

    def test_system_prompt_estavel_e_handoff_dinamico(self):
        runner = RunnerFalso()
        self.rodar(runner)
        analista = runner.chamadas[0]
        self.assertNotIn("DEMO-1", analista.system)
        self.assertIn("DEMO-1", analista.prompt)
        self.assertEqual(analista.ferramentas, ["Read", "Glob", "Grep"])

    def test_revisor_devolve_e_dev_recebe_o_motivo(self):
        runner = RunnerFalso({"revisor": [{"aprovado": False, "motivo": "falta tratar filtro vazio"}]})
        estado, status = self.rodar(runner)
        self.assertEqual(status, "pronto-para-release")
        self.assertEqual(runner.papeis().count("dev"), 2)
        segunda_do_dev = [c for c in runner.chamadas if c.papel == "dev"][1]
        self.assertIn("falta tratar filtro vazio", segunda_do_dev.prompt)
        self.assertEqual(estado["voltas_total"], 1)

    def test_gate_que_falha_uma_vez_se_autocorrige(self):
        marcador = self.tmp / "ja-falhou"
        self.definir_gates(f"test -f {marcador} || (touch {marcador}; echo 'FAIL test_export'; exit 1)", "true")
        runner = RunnerFalso()
        estado, status = self.rodar(runner)
        self.assertEqual(status, "pronto-para-release")
        self.assertEqual(estado["gates"]["gate_stg"], {"falhas": 1, "aprovado": True})
        segunda_do_dev = [c for c in runner.chamadas if c.papel == "dev"][1]
        self.assertIn("FAIL test_export", segunda_do_dev.prompt)

    def test_gate_que_sempre_falha_bloqueia_no_teto_de_voltas(self):
        self.definir_gates("exit 1", "true")
        estado, status = self.rodar(RunnerFalso())
        self.assertEqual(status, "bloqueado")
        self.assertEqual(estado["voltas"], self.cfg["max_voltas"] + 1)
        self.assertIn("bloqueado", (self.vault.raiz / "notificacoes.md").read_text())

    def test_card_sem_criterios_e_rejeitado_sem_chamar_agente(self):
        path = self.vault.raiz / "demandas" / "DEMO-1" / "card.md"
        path.write_text(CARD.replace("- Dado um relatório", "Dado um relatório"))
        runner = RunnerFalso()
        _, status = self.rodar(runner)
        self.assertEqual(status, "rejeitado")
        self.assertEqual(runner.chamadas, [])

    def test_analista_com_perguntas_para_e_espera_humano(self):
        runner = RunnerFalso({"analista": [{"pronto": False, "perguntas": ["CSV com ; ou ,?"]}]})
        estado, status = self.rodar(runner)
        self.assertEqual(status, "bloqueado")
        self.assertEqual(runner.papeis(), ["analista"])
        self.assertIn("CSV com ; ou ,?", self.vault.card("DEMO-1").corpo)

    def test_orcamento_do_card_esgota_e_bloqueia(self):
        self.cfg["orcamento_card_usd"] = 1.0
        runner = RunnerFalso(custo=0.6)
        estado, status = self.rodar(runner)
        self.assertEqual(status, "bloqueado")
        self.assertEqual(runner.papeis(), ["analista", "arquiteto"])
        self.assertLessEqual(runner.chamadas[1].orcamento_usd, 0.4 + 1e-9)

    def test_falha_transitoria_tenta_de_novo(self):
        runner = RunnerFalso({"analista": [Execucao(False, erro="overloaded")]})
        estado, status = self.rodar(runner)
        self.assertEqual(status, "pronto-para-release")
        self.assertEqual(runner.papeis()[:2], ["analista", "analista"])

    def test_retomada_do_checkpoint_apos_bloqueio(self):
        self.definir_gates("true", "exit 1")
        _, status = self.rodar(RunnerFalso())
        self.assertEqual(status, "bloqueado")

        # Uma pessoa corrige o gate e devolve o card à fila.
        self.definir_gates("true", "true")
        card = self.vault.card("DEMO-1")
        self.vault.mudar_status(card, "a-fazer")
        runner = RunnerFalso()
        estado, status = self.rodar(runner)
        # Retoma no gate que bloqueou: nenhum agente roda de novo, nenhum token é gasto.
        self.assertEqual(status, "pronto-para-release")
        self.assertEqual(runner.papeis(), [])
        self.assertTrue(estado["gates"]["gate_qa"]["aprovado"])


if __name__ == "__main__":
    unittest.main()
