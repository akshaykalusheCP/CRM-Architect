import re

MAX_API_NAME = 40  # without the __c suffix
MAX_LABEL = 40


def api_base(text: str, max_len: int = MAX_API_NAME) -> str:
    """snake_case / free text -> Salesforce-safe Title_Case identifier (no suffix)."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", text) if p]
    name = "_".join(p[:1].upper() + p[1:] for p in parts) or "Field"
    if not name[0].isalpha():
        name = "X" + name
    name = re.sub(r"_+", "_", name)[:max_len].rstrip("_")
    return name


def label(text: str, max_len: int = MAX_LABEL) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


class NameRegistry:
    """Hands out unique names within a scope (e.g. objects in an org, fields on an object)."""

    def __init__(self, taken: set[str] | None = None):
        self._taken = {t.lower() for t in (taken or set())}

    def claim(self, base: str, suffix: str = "", max_len: int = MAX_API_NAME) -> str:
        candidate, n = base, 1
        while f"{candidate}{suffix}".lower() in self._taken:
            n += 1
            tail = f"_{n}"
            candidate = base[: max_len - len(tail)].rstrip("_") + tail
        self._taken.add(f"{candidate}{suffix}".lower())
        return f"{candidate}{suffix}"
