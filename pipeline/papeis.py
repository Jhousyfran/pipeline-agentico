"""Os papéis do escritório: ferramentas, contrato de saída e artefato de cada especialista."""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter

LEITURA = ("Read", "Glob", "Grep")
EDICAO = ("Read", "Glob", "Grep", "Edit", "Write", "Bash")


def _schema(**extra) -> dict:
    props = {
        "documento": {"type": "string", "description": "Markdown completo do artefato desta etapa."},
        "resumo": {"type": "string", "description": "Uma linha para o comentário no card."},
        **extra,
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


@dataclass(frozen=True)
class Papel:
    nome: str
    artefato: str
    ferramentas: tuple[str, ...]
    schema: dict

    @property
    def edita(self) -> bool:
        return "Edit" in self.ferramentas


PAPEIS = {
    "analista": Papel("analista", "analise.md", LEITURA, _schema(
        pronto={"type": "boolean", "description": "true se os critérios estão fechados e a story pode seguir."},
        perguntas={"type": "array", "items": {"type": "string"},
                   "description": "O que uma pessoa precisa responder antes de seguir (vazio se pronto)."},
    )),
    "arquiteto": Papel("arquiteto", "adr.md", LEITURA, _schema()),
    "dev": Papel("dev", "plano.md", EDICAO, _schema()),
    "revisor": Papel("revisor", "revisao.md", LEITURA, _schema(
        aprovado={"type": "boolean"},
        motivo={"type": "string", "description": "Se devolver: o que o dev precisa corrigir, objetivamente."},
    )),
    "qa": Papel("qa", "testes.md", EDICAO, _schema()),
}


@dataclass(frozen=True)
class Prompt:
    papel: str
    versao: int
    sha: str
    texto: str


def carregar_prompt(dir_prompts: Path, papel: str) -> Prompt:
    """O system prompt é um artefato versionado: a versão e o hash entram no trace de cada execução."""
    meta, texto = frontmatter.parse((dir_prompts / papel / "system.md").read_text())
    texto = texto.strip()
    return Prompt(papel, int(meta.get("versao", 0)), hashlib.sha256(texto.encode()).hexdigest()[:8], texto)
