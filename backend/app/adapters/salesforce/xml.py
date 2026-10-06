"""Tiny ordered XML builder for Salesforce metadata.

Metadata API is sensitive to element order, so documents are described as ordered lists of
(tag, value) pairs. A value can be a scalar, a nested list of pairs, or None (element skipped).
"""

import xml.etree.ElementTree as ET
from typing import Any

NS = "http://soap.sforce.com/2006/04/metadata"
Spec = list[tuple[str, Any]]


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _fill(parent: ET.Element, spec: Spec) -> None:
    for tag, value in spec:
        if value is None:
            continue
        child = ET.SubElement(parent, tag)
        if isinstance(value, list):
            _fill(child, value)
        else:
            child.text = _text(value)


def render(root_tag: str, spec: Spec) -> str:
    root = ET.Element(root_tag, {"xmlns": NS})
    _fill(root, spec)
    ET.indent(root, space="    ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
