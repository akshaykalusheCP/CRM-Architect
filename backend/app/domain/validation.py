"""Deterministic checks and safe auto-fixes for the canonical business model."""

import re
from dataclasses import asdict, dataclass
from typing import Literal

from app.domain.business_model import (
    ActionType,
    BusinessModel,
    ConditionOperator,
    Entity,
    Field_,
    FieldType,
    PicklistOption,
    Provenance,
    TriggerType,
)

KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PICKLIST_TYPES = {FieldType.picklist, FieldType.multi_picklist}
# Implicit fields every entity has, usable in conditions/reports without being declared.
IMPLICIT_FIELDS = {"name", "owner", "created_date", "last_modified_date", "created_by", "id"}


@dataclass(frozen=True)
class Issue:
    severity: Literal["error", "warning", "info"]
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _dupes(keys: list[str]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for k in keys:
        (dupes if k in seen else seen).add(k)
    return dupes


def _field_keys(entity: Entity) -> set[str]:
    return {f.key for f in entity.fields} | IMPLICIT_FIELDS


def validate_model(model: BusinessModel) -> list[Issue]:
    issues: list[Issue] = []

    def add(severity, code, path, message):
        issues.append(Issue(severity, code, path, message))

    entities = {e.key: e for e in model.entities}

    for label, items in (
        ("entities", model.entities),
        ("processes", model.processes),
        ("automations", model.automations),
        ("roles", model.roles),
        ("integrations", model.integrations),
        ("reports", model.reports),
    ):
        for key in _dupes([i.key for i in items]):
            add("error", "duplicate_key", f"{label}.{key}", f"Duplicate key '{key}' in {label}.")
        for item in items:
            if not KEY_RE.match(item.key):
                add("error", "invalid_key", f"{label}.{item.key}", f"Key '{item.key}' must be snake_case.")

    if not model.entities:
        add("error", "no_entities", "entities", "The model has no entities.")

    # Entities and fields
    master_detail_parents: dict[str, list[str]] = {}
    for e in model.entities:
        path = f"entities.{e.key}"
        if not e.fields:
            add("warning", "empty_entity", path, f"Entity '{e.label}' has no fields besides its name.")
        for key in _dupes([f.key for f in e.fields]):
            add("error", "duplicate_key", f"{path}.fields.{key}", f"Duplicate field key '{key}'.")
        if e.name_is_auto_number and not e.name_auto_number_format:
            add("warning", "missing_format", path, "Auto-number name field has no display format.")
        for f in e.fields:
            fpath = f"{path}.fields.{f.key}"
            if not KEY_RE.match(f.key):
                add("error", "invalid_key", fpath, f"Field key '{f.key}' must be snake_case.")
            if f.key in IMPLICIT_FIELDS:
                add("warning", "implicit_field", fpath, f"'{f.key}' is a built-in field on every entity.")
            if f.type in PICKLIST_TYPES:
                if not f.options:
                    add("error", "picklist_without_options", fpath, f"Picklist '{f.label}' has no options.")
                for v in _dupes([o.value for o in f.options]):
                    add("error", "duplicate_option", fpath, f"Picklist option '{v}' is duplicated.")
            elif f.options:
                add("warning", "options_on_non_picklist", fpath, "Options are ignored for non-picklist fields.")
            if f.type == FieldType.lookup:
                if not f.reference_entity:
                    add("error", "lookup_without_target", fpath, f"Lookup '{f.label}' has no target entity.")
                elif f.reference_entity not in entities:
                    add(
                        "error",
                        "unknown_entity",
                        fpath,
                        f"Lookup '{f.label}' references unknown entity '{f.reference_entity}'.",
                    )
                if f.relationship == "master_detail" and f.reference_entity:
                    master_detail_parents.setdefault(e.key, []).append(f.reference_entity)
            elif f.reference_entity or f.relationship:
                add("warning", "reference_on_non_lookup", fpath, "Reference is ignored for non-lookup fields.")
            if f.type == FieldType.auto_number and not f.auto_number_format:
                add("warning", "missing_format", fpath, "Auto-number field has no display format.")
            if f.unique and f.type not in {FieldType.text, FieldType.email, FieldType.number, FieldType.phone}:
                add("warning", "unique_unsupported", fpath, f"Unique is unusual for {f.type} fields.")
            if f.provenance.confidence < 0.5:
                add("info", "low_confidence", fpath, f"Field '{f.label}' is a low-confidence guess.")

    for child, parents in master_detail_parents.items():
        if len(parents) > 2:
            add(
                "warning",
                "too_many_master_detail",
                f"entities.{child}",
                "More than two master-detail relationships; most CRMs will need some as lookups.",
            )
    # Master-detail cycles
    for start in master_detail_parents:
        stack, seen = list(master_detail_parents.get(start, [])), set()
        while stack:
            node = stack.pop()
            if node == start:
                add("error", "master_detail_cycle", f"entities.{start}", "Master-detail relationships form a cycle.")
                break
            if node not in seen:
                seen.add(node)
                stack.extend(master_detail_parents.get(node, []))

    # Processes
    process_entities: dict[str, int] = {}
    for p in model.processes:
        path = f"processes.{p.key}"
        entity = entities.get(p.entity)
        if not entity:
            add("error", "unknown_entity", path, f"Process '{p.name}' references unknown entity '{p.entity}'.")
            continue
        process_entities[p.entity] = process_entities.get(p.entity, 0) + 1
        field = next((f for f in entity.fields if f.key == p.stage_field), None)
        if not field:
            add("error", "unknown_field", path, f"Stage field '{p.stage_field}' not found on '{p.entity}'.")
        elif field.type != FieldType.picklist:
            add("error", "stage_field_not_picklist", path, f"Stage field '{p.stage_field}' must be a picklist.")
        else:
            option_values = {o.value for o in field.options}
            missing = [s.label for s in p.stages if s.label not in option_values and s.key not in option_values]
            if missing:
                add("warning", "stages_out_of_sync", path, f"Stages missing from picklist: {', '.join(missing)}.")
        if not p.stages:
            add("error", "no_stages", path, f"Process '{p.name}' has no stages.")
        for key in _dupes([s.key for s in p.stages]):
            add("error", "duplicate_key", f"{path}.stages.{key}", f"Duplicate stage '{key}'.")
        if p.stages and not any(s.category in ("won", "lost", "closed") for s in p.stages):
            add("warning", "no_terminal_stage", path, f"Process '{p.name}' has no closing stage.")
    for ent, count in process_entities.items():
        if count > 1:
            add(
                "info",
                "multiple_processes",
                f"entities.{ent}",
                "Several processes on one entity usually means record types are needed.",
            )

    # Automations
    for a in model.automations:
        path = f"automations.{a.key}"
        entity = entities.get(a.entity)
        if not entity:
            add("error", "unknown_entity", path, f"Automation '{a.name}' references unknown entity '{a.entity}'.")
            continue
        fields = _field_keys(entity)
        for c in a.conditions:
            if c.field not in fields:
                add("error", "unknown_field", path, f"Condition field '{c.field}' not found on '{a.entity}'.")
            if c.operator == ConditionOperator.changed and a.trigger.type == TriggerType.record_created:
                add("warning", "changed_on_create", path, "'changed' conditions never fire on create-only triggers.")
            if (
                c.operator
                not in (
                    ConditionOperator.is_blank,
                    ConditionOperator.is_not_blank,
                    ConditionOperator.changed,
                )
                and c.value is None
            ):
                add("warning", "condition_without_value", path, f"Condition on '{c.field}' has no value.")
        if a.trigger.relative_date_field and a.trigger.relative_date_field not in fields:
            add("error", "unknown_field", path, f"Trigger date field '{a.trigger.relative_date_field}' not found.")
        if not a.actions:
            add("error", "no_actions", path, f"Automation '{a.name}' has no actions.")
        for i, act in enumerate(a.actions):
            apath = f"{path}.actions[{i}]"
            if act.type == ActionType.update_field:
                if not act.target_field:
                    add("error", "missing_target_field", apath, "update_field action has no target field.")
                elif act.target_field not in fields:
                    add("error", "unknown_field", apath, f"Target field '{act.target_field}' not found.")
            if act.type == ActionType.create_record:
                if not act.related_entity:
                    add("error", "missing_entity", apath, "create_record action has no entity.")
                elif act.related_entity not in entities:
                    add("error", "unknown_entity", apath, f"Unknown entity '{act.related_entity}'.")

    # Roles
    roles = {r.key: r for r in model.roles}
    for r in model.roles:
        path = f"roles.{r.key}"
        if r.reports_to and r.reports_to not in roles:
            add("error", "unknown_role", path, f"reports_to '{r.reports_to}' is not a role.")
        for p in r.permissions:
            if p.entity not in entities:
                add("error", "unknown_entity", path, f"Permission references unknown entity '{p.entity}'.")
            if (p.create or p.edit or p.delete) and not p.read:
                add("warning", "write_without_read", path, f"Write access on '{p.entity}' without read.")
    for r in model.roles:
        seen, cur = set(), r
        while cur and cur.reports_to:
            if cur.key in seen:
                add("error", "role_cycle", f"roles.{r.key}", "Role hierarchy contains a cycle.")
                break
            seen.add(cur.key)
            cur = roles.get(cur.reports_to)

    # Integrations / reports / mappings
    for i in model.integrations:
        for ent in i.entities:
            if ent not in entities:
                add("error", "unknown_entity", f"integrations.{i.key}", f"Unknown entity '{ent}'.")
    for rep in model.reports:
        path = f"reports.{rep.key}"
        entity = entities.get(rep.entity)
        if not entity:
            add("error", "unknown_entity", path, f"Report '{rep.name}' references unknown entity '{rep.entity}'.")
            continue
        for g in rep.group_by:
            if g not in _field_keys(entity):
                add("warning", "unknown_field", path, f"Group-by field '{g}' not found on '{rep.entity}'.")
        for role in rep.audience:
            if role not in roles:
                add("warning", "unknown_role", path, f"Audience role '{role}' not found.")
    for m in model.data_mappings:
        path = f"data_mappings.{m.source}.{m.column}"
        entity = entities.get(m.entity)
        if not entity:
            add("error", "unknown_entity", path, f"Mapping targets unknown entity '{m.entity}'.")
        elif m.field not in _field_keys(entity):
            add("error", "unknown_field", path, f"Mapping targets unknown field '{m.entity}.{m.field}'.")

    if not model.roles:
        add("warning", "no_roles", "roles", "No roles defined; everyone will get the same access.")
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


def _auto_format(label: str) -> str:
    letters = re.sub(r"[^A-Za-z]", "", label).upper() or "REC"
    return f"{letters[:3]}-{{0000}}"


def normalize_model(model: BusinessModel) -> BusinessModel:
    """Apply safe, deterministic fixes. Returns a new model."""
    m = model.model_copy(deep=True)
    entities = {e.key: e for e in m.entities}

    for e in m.entities:
        if e.name_is_auto_number and not e.name_auto_number_format:
            e.name_auto_number_format = _auto_format(e.label)
        for f in e.fields:
            if f.type == FieldType.auto_number:
                f.auto_number_format = f.auto_number_format or _auto_format(e.label)
                f.unique = False  # auto numbers are unique by construction
            if f.type == FieldType.lookup and f.relationship is None:
                f.relationship = "lookup"
            if f.type != FieldType.lookup:
                f.reference_entity = None
                f.relationship = None
            if f.type not in PICKLIST_TYPES:
                f.options = []
            else:
                defaults = [o for o in f.options if o.is_default]
                for o in defaults[1:]:
                    o.is_default = False

    # Keep stage picklists in sync with process stages (stages are the source of truth).
    for p in m.processes:
        entity = entities.get(p.entity)
        if not entity:
            continue
        field = next((f for f in entity.fields if f.key == p.stage_field), None)
        if field and field.type == FieldType.picklist and p.stages:
            existing_default = next((o.value for o in field.options if o.is_default), None)
            field.options = [
                PicklistOption(value=s.label, label=s.label, is_default=(s.label == existing_default)) for s in p.stages
            ]
            if not any(o.is_default for o in field.options):
                field.options[0].is_default = True

    for r in m.roles:
        for p in r.permissions:
            if p.modify_all:
                p.view_all = p.delete = p.edit = True
            if p.delete:
                p.edit = True
            if p.view_all or p.edit or p.create:
                p.read = True
    return m


def prune_invalid(model: BusinessModel) -> tuple[BusinessModel, list[str]]:
    """Deterministically repair or drop elements with broken references. Returns the model and what changed.

    Used when an LLM repair pass is not affordable (e.g. staged generation on small rate limits).
    """
    m = model.model_copy(deep=True)
    notes: list[str] = []

    def dedupe(items, what):
        seen, out = set(), []
        for item in items:
            if item.key in seen:
                notes.append(f"Dropped duplicate {what} '{item.key}'.")
                continue
            seen.add(item.key)
            out.append(item)
        return out

    m.entities = dedupe(m.entities, "entity")
    entities = {e.key: e for e in m.entities}
    for e in m.entities:
        e.fields = dedupe(e.fields, f"field on {e.key}")
        for f in e.fields:
            if f.type == FieldType.lookup and f.reference_entity not in entities:
                notes.append(f"{e.key}.{f.key}: lookup target '{f.reference_entity}' missing; made a text field.")
                f.type, f.reference_entity, f.relationship = FieldType.text, None, None
            if f.type in PICKLIST_TYPES and not f.options:
                notes.append(f"{e.key}.{f.key}: picklist had no options; made a text field.")
                f.type = FieldType.text

    kept_processes = []
    for p in dedupe(m.processes, "process"):
        entity = entities.get(p.entity)
        if not entity or not p.stages:
            notes.append(f"Dropped process '{p.name}' (unknown entity or no stages).")
            continue
        field = next((f for f in entity.fields if f.key == p.stage_field), None)
        if field is None:
            entity.fields.append(
                Field_(
                    key=p.stage_field,
                    label="Stage",
                    type=FieldType.picklist,
                    options=[PicklistOption(value=s.label, label=s.label) for s in p.stages],
                    provenance=Provenance(source="inferred", confidence=0.7),
                )
            )
            notes.append(f"Added stage field '{p.stage_field}' to {entity.key} for process '{p.name}'.")
        elif field.type != FieldType.picklist:
            field.type = FieldType.picklist
            field.options = [PicklistOption(value=s.label, label=s.label) for s in p.stages]
        kept_processes.append(p)
    m.processes = kept_processes

    kept_automations = []
    for a in dedupe(m.automations, "automation"):
        entity = entities.get(a.entity)
        if not entity:
            notes.append(f"Dropped automation '{a.name}' (unknown entity '{a.entity}').")
            continue
        fields = _field_keys(entity)
        a.conditions = [c for c in a.conditions if c.field in fields]
        if a.trigger.relative_date_field and a.trigger.relative_date_field not in fields:
            a.trigger.relative_date_field = None
        actions = []
        for act in a.actions:
            if act.type == ActionType.update_field and act.target_field not in fields:
                continue
            if act.type == ActionType.create_record and act.related_entity not in entities:
                continue
            actions.append(act)
        if not actions:
            notes.append(f"Dropped automation '{a.name}' (no valid actions).")
            continue
        a.actions = actions
        kept_automations.append(a)
    m.automations = kept_automations

    m.roles = dedupe(m.roles, "role")
    roles = {r.key: r for r in m.roles}
    for r in m.roles:
        if r.reports_to and r.reports_to not in roles:
            r.reports_to = None
        r.permissions = [p for p in r.permissions if p.entity in entities]
    for r in m.roles:  # break hierarchy cycles
        seen, cur = set(), r
        while cur and cur.reports_to:
            if cur.key in seen:
                notes.append(f"Role hierarchy cycle broken at '{cur.key}'.")
                cur.reports_to = None
                break
            seen.add(cur.key)
            cur = roles.get(cur.reports_to)

    m.integrations = dedupe(m.integrations, "integration")
    for i in m.integrations:
        i.entities = [e for e in i.entities if e in entities]
    reports = []
    for rep in dedupe(m.reports, "report"):
        entity = entities.get(rep.entity)
        if not entity:
            continue
        rep.group_by = [g for g in rep.group_by if g in _field_keys(entity)]
        rep.audience = [a for a in rep.audience if a in roles]
        reports.append(rep)
    m.reports = reports
    m.data_mappings = [
        d for d in m.data_mappings if d.entity in entities and d.field in _field_keys(entities[d.entity])
    ]
    return m, notes


PLACEHOLDER = re.compile(r"\{\{\s*([^}]*?)\s*\}\}")


def _readable(text: str | None) -> str | None:
    """Turn template placeholders like {{order_number}} into plain words ("order number")."""
    if not text:
        return text
    return PLACEHOLDER.sub(lambda m: m.group(1).replace("_", " ").replace(".", " ").strip(), text)


def strip_placeholders(model: BusinessModel) -> tuple[BusinessModel, list[str]]:
    """Remove template syntax the model sometimes invents. Values that only make sense as templates
    (a condition compared with {{today_plus_2}}, a field set to {{order_id}}) cannot be built, so they go."""
    m = model.model_copy(deep=True)
    notes: list[str] = []
    kept = []
    for a in m.automations:
        a.name, a.description = _readable(a.name) or a.name, _readable(a.description) or a.description
        conditions = []
        for c in a.conditions:
            if c.value and PLACEHOLDER.search(c.value):
                notes.append(
                    f"Automation '{a.name}': dropped condition on '{c.field}' with a dynamic value ({c.value})."
                )
                continue
            conditions.append(c)
        a.conditions = conditions
        actions = []
        for x in a.actions:
            if x.type == ActionType.update_field and x.value and PLACEHOLDER.search(x.value):
                notes.append(f"Automation '{a.name}': dropped '{x.description}' (sets a dynamic value).")
                continue
            x.description = _readable(x.description) or x.description
            x.subject, x.body, x.value = _readable(x.subject), _readable(x.body), _readable(x.value)
            actions.append(x)
        if not actions:
            notes.append(f"Dropped automation '{a.name}' (nothing left that can be built).")
            continue
        a.actions = actions
        kept.append(a)
    m.automations = kept
    return m, notes
