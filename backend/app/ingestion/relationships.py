"""Detect likely relationships between columns across uploaded files (foreign-key style)."""

from typing import Any

MIN_OVERLAP = 0.8
MIN_VALUES = 3


def infer_relationships(key_values: dict[tuple[str, str, str], set[str]]) -> list[dict[str, Any]]:
    """key_values maps (file, sheet, column) -> distinct lowercased values.

    A column A 'references' column B when most of A's values appear in B and B looks like a key
    (it has at least as many distinct values). Same-file/same-sheet pairs are skipped.
    """
    items = [(k, v) for k, v in key_values.items() if len(v) >= MIN_VALUES]
    found = []
    for child_key, child_vals in items:
        for parent_key, parent_vals in items:
            if child_key[:2] == parent_key[:2] or len(parent_vals) < len(child_vals):
                continue
            overlap = len(child_vals & parent_vals) / len(child_vals)
            if overlap >= MIN_OVERLAP:
                found.append(
                    {
                        "from": f"{child_key[0]} > {child_key[1]} > {child_key[2]}",
                        "to": f"{parent_key[0]} > {parent_key[1]} > {parent_key[2]}",
                        "overlap": round(overlap, 2),
                    }
                )
    found.sort(key=lambda r: -r["overlap"])
    return found[:50]
