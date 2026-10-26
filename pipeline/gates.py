"""Gates: código que aprova. Nenhuma inferência aqui, e nenhum agente aprova o próprio trabalho."""
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .vault import agora


@dataclass
class ResultadoGate:
    ok: bool
    motivo: str
    log: Path
    duracao_s: float


def rodar(comando: str, cwd: Path, log: Path, timeout_s: int) -> ResultadoGate:
    inicio = time.monotonic()
    try:
        proc = subprocess.run(comando, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        saida, ok = proc.stdout + proc.stderr, proc.returncode == 0
        motivo = "" if ok else f"`{comando}` saiu com código {proc.returncode}."
    except subprocess.TimeoutExpired as e:
        saida = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        ok = False
        motivo = f"`{comando}` passou do limite de {timeout_s}s."
    log.write_text(f"$ {comando}\n# {agora()} · cwd {cwd}\n\n{saida}")
    if not ok:
        final = "\n".join(saida.strip().splitlines()[-40:])
        motivo += f" Final do log ({log.name}):\n```\n{final}\n```"
    return ResultadoGate(ok, motivo, log, time.monotonic() - inicio)
