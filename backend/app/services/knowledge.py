"""Small, local knowledge catalog and scoped retrieval for discovery."""

import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeEntry, Project

BUILTIN = [
    (
        "Salesforce standard CRM records",
        "Account represents a company or customer organisation; Contact a person; Lead an unqualified prospect; "
        "Opportunity a qualified potential sale; Case a service request. Reuse standard objects when the "
        "business concept fits. Ask when terms such as customer, dealer, and end buyer are ambiguous.",
    ),
    (
        "Implementation and migration sequence",
        "Confirm goals and processes, map data and access, design objects and automations, cleanse and migrate "
        "records, validate configuration, run user acceptance testing, then train users and launch. Record "
        "unknown requirements as questions and keep unsupported deployment tasks as manual steps.",
    ),
    (
        "Salesforce customization restraint",
        "Prefer existing standard fields and relationships. Create a custom object only for a distinct "
        "business record with its own lifecycle. Avoid copying every spreadsheet column into Salesforce; "
        "identify redundant, calculated, and historical-only columns first.",
    ),
]


def _terms(value: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", value.lower()) if w not in {"the", "and", "for", "with"}}


def _same_industry(entry: KnowledgeEntry, project: Project) -> bool:
    return bool(
        entry.industry and project.industry
        and entry.industry.strip().casefold() == project.industry.strip().casefold()
    )


def _entry_content(entry: KnowledgeEntry, query: set[str]) -> str:
    if entry.kind == "salesforce_category" and entry.structured:
        category = entry.structured
        items = category.get("items", [])
        ranked = sorted(items, key=lambda item: len(query & _terms(" ".join(
            str(value) for value in item.values() if isinstance(value, str)
        ))), reverse=True)
        details = []
        for item in ranked[:12]:
            name = item.get("label") or item.get("name") or item.get("title") or item.get("api_name")
            context = [str(item[key]) for key in ("object", "triggerType", "processType", "reportType",
                                                 "authenticationProtocol", "status") if item.get(key)]
            details.append(f"{name} ({', '.join(context)})" if context else str(name))
        return (f"{category.get('count', len(items))} {category.get('title', 'items')}: "
                + ", ".join(details))[:1200]
    if entry.kind != "salesforce_metadata" or not entry.structured:
        return entry.content[:1200]
    obj = entry.structured
    def relevance(field: dict) -> int:
        name = " ".join(str(field.get(k) or "") for k in ("api_name", "label", "description"))
        return len(query & _terms(name))

    fields = sorted(obj.get("fields", []), key=relevance, reverse=True)
    lines = [f"Object API: {obj.get('object_api_name')}; purpose: {(obj.get('description') or '')[:250]}"]
    targets = (obj.get("analysis") or {}).get("relationship_targets", [])
    if targets:
        lines.append("Related to: " + ", ".join(targets[:8]))
    if obj.get("record_types"):
        lines.append("Record types: " + ", ".join(
            r.get("label") or r.get("api_name") or "unknown" for r in obj["record_types"][:8]
        ))
    if obj.get("flows"):
        lines.append("Record-triggered flows: " + ", ".join(
            flow.get("label") or flow.get("api_name") or "unknown" for flow in obj["flows"][:8]
        ))
    if obj.get("validation_rules"):
        lines.append("Validation rules: " + ", ".join(
            rule.get("api_name") or "unknown" for rule in obj["validation_rules"][:8]
        ))
    lifecycle = (obj.get("analysis") or {}).get("lifecycle_candidates", [])
    if lifecycle:
        lines.append("Possible lifecycle fields: " + ", ".join(lifecycle[:6]))
    for field in fields[:12]:
        target = f", lookup to {field['reference_to']}" if field.get("reference_to") else ""
        values = f", values: {'|'.join(field['values'][:6])}" if field.get("values") else ""
        lines.append(
            f"{field.get('label') or field.get('api_name')} ({field.get('api_name')}, "
            f"{field.get('type') or 'unknown'}{target}{values})"
        )
    return "; ".join(lines)[:1200]


async def retrieve_knowledge(session: AsyncSession, project: Project, source_text: str = "") -> list[dict]:
    rows = (
        await session.scalars(
            select(KnowledgeEntry).where(
                KnowledgeEntry.org_id == project.org_id,
                KnowledgeEntry.status == "approved",
                or_(KnowledgeEntry.project_id.is_(None), KnowledgeEntry.project_id == project.id),
            )
        )
    ).all()
    rows = [
        row for row in rows
        if row.project_id == project.id or not row.industry
        or _same_industry(row, project)
    ]
    query = _terms(" ".join((project.industry or "", project.description[:5000], source_text[:3000])))
    ranked = sorted(
        rows,
        key=lambda row: (
            row.project_id == project.id,
            _same_industry(row, project),
            len(query & _terms(row.title + " " + row.content)),
            row.created_at,
        ),
        reverse=True,
    )
    # Keep prompts small for free-tier providers. Parsed metadata remains fully browseable in the UI.
    return [
        {"id": f"builtin:{i}", "title": title, "content": content, "kind": "built_in"}
        for i, (title, content) in enumerate(BUILTIN)
    ] + [
        {
            "id": row.id,
            "title": row.title,
            "content": _entry_content(row, query),
            "kind": row.kind,
            "industry": row.industry,
        }
        for row in ranked[:6]
        if row.project_id == project.id
        or _same_industry(row, project)
        or query & _terms(row.title + " " + row.content)
    ]


def render_knowledge(entries: list[dict]) -> str:
    if not entries:
        return ""
    lines = [
        "Reviewed implementation knowledge. Client evidence and answers take priority. Imported Salesforce "
        "metadata is an example from another implementation, not a requirement to copy its objects or fields."
    ]
    lines += [
        f"- [{entry['id']}] {entry['title']} ({entry.get('kind', 'guidance')}, "
        f"{entry.get('industry') or 'all industries'}): {entry['content']}"
        for entry in entries
    ]
    return "\n".join(lines)
