"""O vault: cards, documentos do projeto, artefatos, checkpoints e trace.

Tudo é MD ou JSON num diretório que é um repositório git (e abre no Obsidian). É o estado persistido
na fronteira entre agentes e a memória de longo prazo do escritório.
"""
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import frontmatter
from .git import git

DOCS_PROJETO = ("PRD.md", "SPEC.md", "TASKS.md", "WORKFLOW.md")


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Card:
    path: Path
    meta: dict
    corpo: str

    @property
    def id(self) -> str:
        return str(self.meta["id"])

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    def secao(self, titulo: str) -> str:
        linhas, dentro = [], False
        for linha in self.corpo.splitlines():
            if linha.startswith("## "):
                dentro = linha[3:].strip().lower() == titulo.lower()
            elif dentro:
                linhas.append(linha)
        return "\n".join(linhas).strip()

    def criterios(self) -> list[str]:
        return [l[2:].strip() for l in self.secao("Critérios de aceite").splitlines()
                if l.startswith("- ") and l[2:].strip()]


class Vault:
    def __init__(self, raiz: Path):
        self.raiz = raiz

    # ---- cards ---------------------------------------------------------------

    def cards(self) -> list[Card]:
        return [self._ler(p) for p in sorted(self.raiz.glob("demandas/*/card.md"))]

    def card(self, card_id: str) -> Card:
        path = self.raiz / "demandas" / card_id / "card.md"
        if not path.exists():
            raise KeyError(f"card {card_id} não encontrado em {path}")
        return self._ler(path)

    def _ler(self, path: Path) -> Card:
        meta, corpo = frontmatter.parse(path.read_text())
        return Card(path, meta, corpo)

    def salvar_card(self, card: Card) -> None:
        card.path.write_text(frontmatter.dump(card.meta, card.corpo))

    def mudar_status(self, card: Card, status: str) -> None:
        card.meta["status"] = status
        card.meta["atualizado"] = agora()
        self.salvar_card(card)

    def registrar(self, card: Card, autor: str, mensagem: str) -> None:
        """O comentário no card: cada agente diz o que fez e aponta a evidência."""
        if "## Registro" not in card.corpo:
            card.corpo = card.corpo.rstrip() + "\n\n## Registro\n"
        mensagem = mensagem.strip().replace("\n", "\n  ")  # continua dentro do item da lista
        card.corpo = card.corpo.rstrip("\n") + f"\n- {agora()} · **{autor}** · {mensagem}\n"
        self.salvar_card(card)

    # ---- projeto -------------------------------------------------------------

    def projeto(self, nome: str) -> tuple[dict, list[Path]]:
        """Devolve o frontmatter do WORKFLOW.md (repo, branch, gates) e os quatro documentos que antecedem a story."""
        pasta = self.raiz / "projetos" / nome
        workflow = pasta / "WORKFLOW.md"
        if not nome or not workflow.exists():
            raise FileNotFoundError(f"projeto '{nome}' sem WORKFLOW.md em {pasta}")
        meta, _ = frontmatter.parse(workflow.read_text())
        # No WORKFLOW todo valor é texto: `gate_qa: true` é o comando `true`, não um booleano.
        meta = {chave: frontmatter.formatar(valor) for chave, valor in meta.items()}
        return meta, [pasta / d for d in DOCS_PROJETO if (pasta / d).exists()]

    # ---- artefatos, checkpoint e trace --------------------------------------

    def escrever_artefato(self, card: Card, nome: str, meta: dict, conteudo: str) -> Path:
        path = card.dir / nome
        path.write_text(frontmatter.dump(meta, conteudo))
        return path

    def evidencia(self, card: Card, nome: str) -> Path:
        pasta = card.dir / "evidencias"
        pasta.mkdir(exist_ok=True)
        return pasta / nome

    def ler_estado(self, card: Card) -> dict | None:
        path = card.dir / "estado.json"
        return json.loads(path.read_text()) if path.exists() else None

    def salvar_estado(self, card: Card, estado: dict) -> None:
        """Checkpoint: escrita atômica, para que um processo que cai nunca deixe estado pela metade."""
        path = card.dir / "estado.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(estado, indent=2, ensure_ascii=False))
        tmp.replace(path)

    def trace(self, card: Card, registro: dict) -> None:
        with (card.dir / "trace.jsonl").open("a") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")

    # ---- avisos e versionamento --------------------------------------------

    def notificar(self, evento: str, card: Card, mensagem: str, comando: str = "") -> None:
        """Registra o aviso no vault e, se houver `notificar_cmd`, entrega por ele (e-mail, Slack, ntfy...)."""
        assunto = f"[{evento}] {card.id} · {card.meta.get('titulo', '')}"
        with (self.raiz / "notificacoes.md").open("a") as f:
            f.write(f"- {agora()} · **{assunto}** · {mensagem.splitlines()[0] if mensagem else ''}\n")
        if comando:
            env = {**os.environ, "PIPELINE_EVENTO": evento, "PIPELINE_CARD": card.id, "PIPELINE_ASSUNTO": assunto}
            subprocess.run(comando, shell=True, input=mensagem, text=True, env=env, timeout=60, check=False)

    def commit(self, mensagem: str) -> None:
        """Cada escrita do escritório é um commit no vault."""
        if not (self.raiz / ".git").exists():
            return
        git(self.raiz, "add", "-A")
        git(self.raiz, "commit", "-q", "-m", mensagem, check=False)

    def sincronizar(self, direcao: str) -> str | None:
        """`pull` traz cards criados em outra máquina; `push` publica o que o escritório fez.

        Devolve o erro em vez de levantar: o trabalho já está commitado localmente e sobe na próxima passada.
        """
        if not (self.raiz / ".git").exists():
            return "o vault não é um repositório git"
        args = ("pull", "--rebase", "--autostash", "-q") if direcao == "pull" else ("push", "-q")
        r = git(self.raiz, *args, check=False)
        return None if r.returncode == 0 else (r.stderr.strip() or f"git {direcao} saiu com código {r.returncode}")
