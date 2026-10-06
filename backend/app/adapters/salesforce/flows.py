"""Canonical automations -> record-triggered Flows (deployed as Draft for consultant review)."""

from dataclasses import dataclass, field

from app.adapters.salesforce.naming import api_base
from app.adapters.salesforce.planner import SfObject, SfPlan
from app.adapters.salesforce.xml import Spec, render
from app.domain.business_model import (
    Action,
    ActionType,
    Automation,
    Condition,
    ConditionOperator,
    FieldType,
    TriggerType,
)

OPERATORS = {
    ConditionOperator.equals: "EqualTo",
    ConditionOperator.not_equals: "NotEqualTo",
    ConditionOperator.greater_than: "GreaterThan",
    ConditionOperator.less_than: "LessThan",
    ConditionOperator.contains: "Contains",
}
TRIGGER_TYPES = {
    TriggerType.record_created: "Create",
    TriggerType.record_updated: "Update",
    TriggerType.record_created_or_updated: "CreateAndUpdate",
}
WHO_OBJECTS = {"Contact", "Lead"}


@dataclass
class FlowResult:
    api_name: str | None
    xml: str | None
    generated_actions: list[str] = field(default_factory=list)
    manual_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _value(plan: SfPlan, obj: SfObject, field_key: str, raw: str | None) -> Spec:
    sf = obj.fields.get(field_key)
    ftype = sf.canonical.type if sf and sf.canonical else FieldType.text
    raw = raw if raw is not None else ""
    if sf and sf.canonical and sf.canonical.options:
        match = next((o.value for o in sf.canonical.options if raw.lower() in (o.label.lower(), o.value.lower())), None)
        raw = match or raw
    if ftype in (FieldType.number, FieldType.currency, FieldType.percent):
        return [("numberValue", raw)]
    if ftype == FieldType.boolean:
        return [("booleanValue", str(raw).strip().lower() in ("true", "yes", "1"))]
    if ftype == FieldType.date:
        return [("dateValue", raw)]
    if ftype == FieldType.datetime:
        return [("dateTimeValue", raw)]
    return [("stringValue", raw)]


def _filter(plan: SfPlan, obj: SfObject, c: Condition) -> Spec | None:
    api = plan.field_api(obj.entity.key, c.field)
    if not api:
        return None
    if c.operator == ConditionOperator.is_blank:
        return [("field", api), ("operator", "IsNull"), ("value", [("booleanValue", True)])]
    if c.operator == ConditionOperator.is_not_blank:
        return [("field", api), ("operator", "IsNull"), ("value", [("booleanValue", False)])]
    if c.operator == ConditionOperator.changed:
        return [("field", api), ("operator", "IsChanged"), ("value", [("booleanValue", True)])]
    return [("field", api), ("operator", OPERATORS[c.operator]), ("value", _value(plan, obj, c.field, c.value))]


def _node(name: str, label: str) -> Spec:
    return [("name", name), ("label", label[:255]), ("locationX", 0), ("locationY", 0)]


def build_flow(plan: SfPlan, automation: Automation, api_version: str) -> FlowResult:
    obj = plan.objects.get(automation.entity)
    flow_name = api_base(f"{obj.api_name.removesuffix('__c') if obj else ''}_{automation.key}", 80)
    res = FlowResult(api_name=None, xml=None)
    title = f"Automation '{automation.name}'"
    if not obj:
        res.warnings.append(f"{title}: entity not mapped; skipped.")
        return res

    trigger = automation.trigger
    if trigger.type not in TRIGGER_TYPES or trigger.relative_date_field:
        res.manual_steps.append(
            f"{title} on {obj.api_name}: build as a "
            + (
                "scheduled-triggered flow"
                if trigger.type == TriggerType.scheduled
                else "record-triggered flow with a scheduled path"
                if trigger.relative_date_field
                else "before-delete flow"
            )
            + f" - {automation.description}"
        )
        return res

    filters: list[Spec] = []
    for c in automation.conditions:
        if c.operator == ConditionOperator.changed and trigger.type == TriggerType.record_created:
            res.warnings.append(f"{title}: 'changed' condition ignored on a create-only trigger.")
            continue
        spec = _filter(plan, obj, c)
        if spec is None:
            res.warnings.append(f"{title}: condition field '{c.field}' not mapped; condition dropped.")
            continue
        filters.append(spec)

    updates: list[Action] = [a for a in automation.actions if a.type == ActionType.update_field]
    others = [a for a in automation.actions if a.type != ActionType.update_field]
    before_save = bool(updates) and not others

    elements: dict[str, list[Spec]] = {
        "actionCalls": [],
        "assignments": [],
        "formulas": [],
        "recordCreates": [],
        "recordUpdates": [],
    }
    chain: list[tuple[str, str]] = []  # (element type, name) in execution order

    assignment_items: list[Spec] = []
    update_items: list[Spec] = []
    for a in updates:
        api = plan.field_api(obj.entity.key, a.target_field or "")
        if not api:
            res.warnings.append(f"{title}: target field '{a.target_field}' not mapped; action skipped.")
            continue
        value = _value(plan, obj, a.target_field or "", a.value)
        if before_save:
            assignment_items.append([("assignToReference", f"$Record.{api}"), ("operator", "Assign"), ("value", value)])
        else:
            update_items.append([("field", api), ("value", value)])
        res.generated_actions.append(a.description)
    if assignment_items:
        elements["assignments"].append(
            _node("Set_Field_Values", "Set Field Values") + [("assignmentItems", i) for i in assignment_items]
        )
        chain.append(("assignments", "Set_Field_Values"))
    if update_items:
        elements["recordUpdates"].append(
            _node("Update_Triggering_Record", "Update Triggering Record")
            + [("inputAssignments", i) for i in update_items]
            + [("inputReference", "$Record")]
        )
        chain.append(("recordUpdates", "Update_Triggering_Record"))

    for i, a in enumerate(others, start=1):
        if a.type == ActionType.create_task:
            name = f"Create_Task_{i}"
            link_field = "WhoId" if obj.api_name in WHO_OBJECTS else "WhatId"
            assigns: list[Spec] = [
                [("field", "Subject"), ("value", [("stringValue", (a.subject or a.description)[:255])])],
                [("field", link_field), ("value", [("elementReference", "$Record.Id")])],
            ]
            if a.due_in_days is not None:
                formula = f"Due_Date_{i}"
                elements["formulas"].append(
                    [("name", formula), ("dataType", "Date"), ("expression", f"TODAY() + {int(a.due_in_days)}")]
                )
                assigns.append([("field", "ActivityDate"), ("value", [("elementReference", formula)])])
            if obj.has_owner:
                assigns.append([("field", "OwnerId"), ("value", [("elementReference", "$Record.OwnerId")])])
            elements["recordCreates"].append(
                _node(name, a.description)
                + [("inputAssignments", x) for x in assigns]
                + [("object", "Task"), ("storeOutputAutomatically", True)]
            )
            chain.append(("recordCreates", name))
            res.generated_actions.append(a.description)
        elif a.type == ActionType.send_email:
            recipient_ref = None
            if a.recipient == "owner" and obj.has_owner:
                recipient_ref = "$Record.Owner:User.Email"
            elif a.recipient:
                sf = obj.fields.get(a.recipient)
                if sf and sf.canonical and sf.canonical.type == FieldType.email:
                    recipient_ref = f"$Record.{sf.api_name}"
            if not recipient_ref:
                res.manual_steps.append(
                    f"{title}: configure the e-mail recipient ('{a.recipient}') for '{a.description}'."
                )
                continue
            name = f"Send_Email_{i}"
            params = [
                [("name", "emailAddresses"), ("value", [("elementReference", recipient_ref)])],
                [("name", "emailSubject"), ("value", [("stringValue", (a.subject or a.description)[:255])])],
                [("name", "emailBody"), ("value", [("stringValue", a.body or a.description)])],
            ]
            elements["actionCalls"].append(
                _node(name, a.description)
                + [("actionName", "emailSimple"), ("actionType", "emailSimple")]
                + [("inputParameters", p) for p in params]
                + [("storeOutputAutomatically", True)]
            )
            chain.append(("actionCalls", name))
            res.generated_actions.append(a.description)
        elif a.type == ActionType.create_record:
            target = plan.objects.get(a.related_entity or "")
            back_ref = None
            if target and not target.standard:
                back_ref = next(
                    (
                        f
                        for f in target.custom_fields
                        if f.reference_to == obj.api_name and f.sf_type in ("Lookup", "MasterDetail")
                    ),
                    None,
                )
            other_md = target and any(
                f.is_master_detail and f.reference_to != obj.api_name for f in target.custom_fields
            )
            required_missing = target and any(f.required and f is not back_ref for f in target.custom_fields)
            if not target or target.standard or not back_ref or other_md or required_missing:
                res.manual_steps.append(
                    f"{title}: add a Create Records element for '{a.description}' (needs values for required fields)."
                )
                continue
            name = f"Create_Record_{i}"
            assigns = [[("field", back_ref.api_name), ("value", [("elementReference", "$Record.Id")])]]
            if not target.entity.name_is_auto_number:
                assigns.append(
                    [("field", "Name"), ("value", [("stringValue", (a.subject or target.entity.label)[:80])])]
                )
            elements["recordCreates"].append(
                _node(name, a.description)
                + [("inputAssignments", x) for x in assigns]
                + [("object", target.api_name), ("storeOutputAutomatically", True)]
            )
            chain.append(("recordCreates", name))
            res.generated_actions.append(a.description)
        else:
            hint = {
                ActionType.assign_owner: "use Lead/Case assignment rules, Omni-Channel, or a flow with a queue",
                ActionType.notify_user: "create a Custom Notification Type and add a Send Custom Notification action",
                ActionType.call_webhook: "use an Outbound Message or an HTTP Callout action with a Named Credential",
            }.get(a.type, "configure manually")
            res.manual_steps.append(f"{title}: '{a.description}' - {hint}.")

    if not chain:
        return res

    # Wire connectors in order.
    for idx, (etype, name) in enumerate(chain[:-1]):
        nxt = chain[idx + 1][1]
        for el in elements[etype]:
            if el[0] == ("name", name):
                connector = ("connector", [("targetReference", nxt)])
                if etype == "assignments":
                    el.append(connector)  # assignmentItems sort before connector
                else:
                    # after name/label/locationX/locationY (+ actionName/actionType for actions)
                    el.insert(6 if etype == "actionCalls" else 4, connector)

    record_trigger = TRIGGER_TYPES[trigger.type]
    has_changed = any(f[1] == ("operator", "IsChanged") for f in filters)
    start: Spec = [
        ("locationX", 0),
        ("locationY", 0),
        ("connector", [("targetReference", chain[0][1])]),
    ]
    if filters and record_trigger != "Create" and not has_changed:
        start.append(("doesRequireRecordChangedToMeetCriteria", True))
    if filters:
        start.append(("filterLogic", "and" if automation.condition_logic == "all" else "or"))
        start += [("filters", f) for f in filters]
    start += [
        ("object", obj.api_name),
        ("recordTriggerType", record_trigger),
        ("triggerType", "RecordBeforeSave" if before_save else "RecordAfterSave"),
    ]

    meta = [
        ("processMetadataValues", [("name", n), ("value", [("stringValue", v)])])
        for n, v in (
            ("BuilderType", "LightningFlowBuilder"),
            ("CanvasMode", "AUTO_LAYOUT_CANVAS"),
            ("OriginBuilderType", "LightningFlowBuilder"),
        )
    ]
    spec: Spec = (
        [("actionCalls", e) for e in elements["actionCalls"]]
        + [("apiVersion", api_version)]
        + [("assignments", e) for e in elements["assignments"]]
        + [("description", automation.description[:2000])]
        + [("environments", "Default")]
        + [("formulas", e) for e in elements["formulas"]]
        + [("interviewLabel", f"{automation.name[:200]} {{!$Flow.CurrentDateTime}}")]
        + [("label", automation.name[:255])]
        + meta
        + [("processType", "AutoLaunchedFlow")]
        + [("recordCreates", e) for e in elements["recordCreates"]]
        + [("recordUpdates", e) for e in elements["recordUpdates"]]
        + [("start", start)]
        + [("status", "Draft")]
    )
    res.api_name = flow_name
    res.xml = render("Flow", spec)
    return res
