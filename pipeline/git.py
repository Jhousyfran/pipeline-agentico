"""Git determinístico: worktree por card, commit e diff. Os agentes não fazem commit; o orquestrador faz."""
import subprocess
from pathlib import Path

# Todo commit feito pelo escritório é identificável no histórico.
IDENTIDADE = ["-c", "user.name=pipeline-agentica", "-c", "user.email=pipeline-agentica@localhost"]


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *IDENTIDADE, *args], cwd=cwd, check=check, capture_output=True, text=True)


def rev_parse(cwd: Path, ref: str) -> str:
    return git(cwd, "rev-parse", ref).stdout.strip()


def preparar_worktree(repo: Path, destino: Path, branch: str, base_sha: str) -> None:
    """Idempotente: na retomada, a worktree e a branch do card já existem e são reaproveitadas."""
    if destino.exists():
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    if git(repo, "rev-parse", "--verify", "--quiet", branch, check=False).returncode == 0:
        git(repo, "worktree", "add", str(destino), branch)
    else:
        git(repo, "worktree", "add", "-b", branch, str(destino), base_sha)


def commit_tudo(cwd: Path, mensagem: str) -> str | None:
    git(cwd, "add", "-A")
    if git(cwd, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    git(cwd, "commit", "-q", "-m", mensagem)
    return rev_parse(cwd, "HEAD")


def diff(cwd: Path, base_sha: str) -> str:
    return git(cwd, "diff", f"{base_sha}...HEAD").stdout
