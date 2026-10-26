"""Frontmatter mínimo (chave: valor) para os MD do vault, sem dependências."""


def parse(texto: str) -> tuple[dict, str]:
    if not texto.startswith("---\n"):
        return {}, texto
    fim = texto.find("\n---", 3)
    if fim == -1:
        return {}, texto
    meta = {}
    for linha in texto[4:fim].splitlines():
        if ":" not in linha or linha.lstrip().startswith("#"):
            continue
        chave, valor = linha.split(":", 1)
        meta[chave.strip()] = _valor(valor.strip())
    return meta, texto[fim + 4:].lstrip("\n")


def dump(meta: dict, corpo: str) -> str:
    linhas = [f"{chave}: {formatar(valor)}" for chave, valor in meta.items()]
    return "---\n" + "\n".join(linhas) + "\n---\n\n" + corpo.lstrip("\n")


def _valor(v: str):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    for tipo in (int, float):
        try:
            return tipo(v)
        except ValueError:
            pass
    return v


def formatar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    return str(v).replace("\n", " ")
