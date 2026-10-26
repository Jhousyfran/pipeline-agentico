"""Invoca um agente: Claude Code em modo headless, com contexto enxuto e saída estruturada."""
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

# Sem estas flags cada chamada herda o system prompt padrão, plugins, MCPs e skills da máquina:
# medimos ~89 mil tokens de prefixo numa chamada trivial, contra ~1,1 mil no modo enxuto.
FLAGS_ENXUTAS = [
    "--no-session-persistence",
    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    "--setting-sources", "",
    "--disable-slash-commands",
    "--permission-prompts", "none",  # o que pediria permissão é negado, nunca fica esperando alguém
]


@dataclass
class Chamada:
    papel: str
    system: str          # prefixo estável: o system prompt versionado, sem data nem IDs
    prompt: str          # o handoff: identificador, objetivo e referências
    modelo: str
    ferramentas: list[str]
    permitidas: list[str]
    modo_permissao: str
    schema: dict
    orcamento_usd: float
    cwd: Path
    dirs_extra: list[Path] = field(default_factory=list)


@dataclass
class Execucao:
    ok: bool
    saida: dict | None = None
    custo_usd: float = 0.0
    tokens: dict = field(default_factory=dict)
    duracao_s: float = 0.0
    turnos: int = 0
    negacoes: int = 0
    erro: str | None = None
    orcamento_estourado: bool = False


class ClaudeRunner:
    def __init__(self, claude_bin: str = "claude", timeout_s: int = 1800):
        self.claude_bin = claude_bin
        self.timeout_s = timeout_s

    def executar(self, c: Chamada) -> Execucao:
        cmd = [
            self.claude_bin, "-p",
            "--output-format", "json",
            "--model", c.modelo,
            "--system-prompt", c.system,
            "--tools", ",".join(c.ferramentas),
            "--permission-mode", c.modo_permissao,
            "--max-budget-usd", f"{c.orcamento_usd:.2f}",
            "--json-schema", json.dumps(c.schema),
            *FLAGS_ENXUTAS,
        ]
        if c.permitidas:
            cmd += ["--allowed-tools", *c.permitidas]
        if c.dirs_extra:
            cmd += ["--add-dir", *map(str, c.dirs_extra)]

        inicio = time.monotonic()
        try:
            # O prompt vai por stdin: as opções variádicas acima não o engolem como argumento.
            proc = subprocess.run(cmd, input=c.prompt, capture_output=True, text=True,
                                  cwd=c.cwd, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            return Execucao(False, erro=f"watchdog: sem resposta em {self.timeout_s}s",
                            duracao_s=time.monotonic() - inicio)
        duracao = time.monotonic() - inicio

        try:
            dados = json.loads(proc.stdout)
        except json.JSONDecodeError:
            detalhe = (proc.stderr or proc.stdout)[-500:]
            return Execucao(False, erro=f"saída não é JSON (exit {proc.returncode}): {detalhe}", duracao_s=duracao)

        uso = dados.get("usage") or {}
        ex = Execucao(
            ok=False,
            custo_usd=float(dados.get("total_cost_usd") or 0.0),
            tokens={k: uso.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                "cache_read_input_tokens", "output_tokens")},
            duracao_s=duracao,
            turnos=dados.get("num_turns", 0),
            negacoes=len(dados.get("permission_denials") or []),
        )
        subtipo = str(dados.get("subtype", ""))
        if dados.get("is_error") or subtipo != "success":
            ex.erro = f"{subtipo or 'erro'}: {str(dados.get('result', ''))[:300]}"
            ex.orcamento_estourado = "budget" in subtipo
            return ex
        if not isinstance(dados.get("structured_output"), dict):
            ex.erro = "resposta sem structured_output"
            return ex
        ex.ok, ex.saida = True, dados["structured_output"]
        return ex
