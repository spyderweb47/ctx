from . import claude_code, codex

ADAPTERS = {a.NAME: a for a in (claude_code, codex)}


def discover_all():
    out = []
    for a in ADAPTERS.values():
        try:
            out.extend(a.discover())
        except Exception:
            continue          # a broken/absent store must never kill the tool
    out.sort(key=lambda r: str(r.updated_at), reverse=True)
    return out
