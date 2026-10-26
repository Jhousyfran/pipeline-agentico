"""Sincronização do vault entre duas máquinas por um remoto git."""
import tempfile
import unittest
from pathlib import Path

from pipeline.git import git
from pipeline.vault import Vault


class TesteSincronizacao(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.remoto = tmp / "remoto.git"
        git(tmp, "init", "-q", "--bare", "-b", "main", str(self.remoto))
        self.processa = self._clone(tmp / "maquina-a")
        self.cria_cards = self._clone(tmp / "maquina-b")

    def _clone(self, destino: Path) -> Vault:
        git(destino.parent, "clone", "-q", str(self.remoto), str(destino))
        git(destino, "checkout", "-q", "-B", "main")
        return Vault(destino)

    def _commit(self, vault: Vault, arquivo: str, mensagem: str) -> None:
        path = vault.raiz / arquivo
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mensagem)
        vault.commit(mensagem)

    def test_card_criado_em_outra_maquina_chega_no_pull_e_o_trabalho_volta_no_push(self):
        self._commit(self.cria_cards, "demandas/APP-1/card.md", "APP-1: card criado")
        git(self.cria_cards.raiz, "push", "-q", "-u", "origin", "main")

        self.assertIsNone(self.processa.sincronizar("pull"))
        self.assertTrue((self.processa.raiz / "demandas/APP-1/card.md").exists())

        self._commit(self.processa, "demandas/APP-1/estado.json", "APP-1: analise → desenho")
        self.assertIsNone(self.processa.sincronizar("push"))

        self.assertIsNone(self.cria_cards.sincronizar("pull"))
        self.assertTrue((self.cria_cards.raiz / "demandas/APP-1/estado.json").exists())

    def test_falha_de_push_devolve_o_erro_sem_levantar(self):
        self._commit(self.processa, "notificacoes.md", "local")
        git(self.processa.raiz, "remote", "set-url", "origin", "/caminho/que/nao/existe")
        erro = self.processa.sincronizar("push")
        self.assertIsNotNone(erro)

    def test_vault_sem_git_nao_sincroniza(self):
        self.assertIsNotNone(Vault(Path(tempfile.mkdtemp())).sincronizar("pull"))


if __name__ == "__main__":
    unittest.main()
