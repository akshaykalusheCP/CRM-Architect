"""Generate a deployable SFDX (source format) project from the canonical business model."""

import csv
import io
import json
from collections import defaultdict

from app.adapters.base import ExportArtifact, PlatformAdapter
from app.adapters.salesforce import standard as std
from app.adapters.salesforce.flows import build_flow
from app.adapters.salesforce.naming import NameRegistry, api_base, label
from app.adapters.salesforce.planner import SfField, SfObject, SfPlan, build_plan
from app.adapters.salesforce.xml import Spec, render
from app.core.config import get_settings
from app.domain.business_model import BusinessModel, Process

SRC = "force-app/main/default"
TAB_MOTIFS = ["Custom13: Box", "Custom57: Building", "Custom62: Books"]
FORECAST = {"won": "Closed", "lost": "Omitted", "closed": "Omitted"}


def _picklist_values(f) -> Spec:
    values = [
        ("value", [("fullName", o.value[:255]), ("default", o.is_default), ("label", o.label[:255])])
        for o in f.options[:1000]
    ]
    return [("restricted", True), ("valueSetDefinition", [("sorted", False)] + values)]


def _field_xml(obj: SfObject, sf: SfField) -> str:
    f = sf.canonical
    assert f is not None and sf.sf_type
    t = sf.sf_type
    spec: Spec = [("fullName", sf.api_name)]
    if f.default_value and t in ("Number", "Currency", "Percent"):
        try:
            spec.append(("defaultValue", str(float(f.default_value.replace(",", "")))))
        except ValueError:
            pass
    elif f.default_value and t in ("Text", "TextArea", "Email", "Phone", "Url"):
        spec.append(("defaultValue", json.dumps(f.default_value)))
    elif t == "Checkbox":
        spec.append(("defaultValue", str(f.default_value).strip().lower() in ("true", "yes", "1")))
    if t in ("Lookup",):
        spec.append(("deleteConstraint", "Restrict" if sf.required else "SetNull"))
    if f.description:
        spec.append(("description", f.description[:1000]))
    if t == "AutoNumber":
        spec.append(("displayFormat", f.auto_number_format or f"{api_base(obj.entity.key)[:3].upper()}-{{0000}}"))
    if f.external_id and t in ("Text", "Number", "Email", "AutoNumber"):
        spec.append(("externalId", True))
    if f.description:
        spec.append(("inlineHelpText", f.description[:510]))
    spec.append(("label", label(f.label)))
    if t == "Text":
        spec.append(("length", min(f.length or 255, 255)))
    elif t in ("LongTextArea", "Html"):
        spec.append(("length", 32768))
    if t in ("Number", "Currency", "Percent"):
        scale = f.scale if f.scale is not None else (2 if t != "Number" else 0)
        spec.append(("precision", 18))
    if t == "MasterDetail":
        spec += [
            ("referenceTo", sf.reference_to),
            ("relationshipLabel", label(obj.entity.plural_label)),
            ("relationshipName", sf.relationship_name),
            ("relationshipOrder", sf.relationship_order),
            ("reparentableMasterDetail", False),
        ]
    elif t == "Lookup":
        spec += [
            ("referenceTo", sf.reference_to),
            ("relationshipLabel", label(obj.entity.plural_label)),
            ("relationshipName", sf.relationship_name),
        ]
    if t not in ("Checkbox", "MasterDetail", "AutoNumber"):
        spec.append(("required", sf.required))
    if t in ("Number", "Currency", "Percent"):
        spec.append(("scale", scale))
    spec.append(("type", t))
    if t in ("Text", "Number", "Email") and f.unique:
        spec.append(("unique", True))
    if t in ("Picklist", "MultiselectPicklist"):
        spec.append(("valueSet", _picklist_values(f)))
    if t in ("LongTextArea", "Html", "MultiselectPicklist"):
        spec.append(("visibleLines", 10 if t != "MultiselectPicklist" else 4))
    if t == "MasterDetail":
        spec.append(("writeRequiresMasterRead", False))
    return render("CustomField", spec)


def _legacy_field_xml(api: str) -> str:
    return render(
        "CustomField",
        [
            ("fullName", api),
            ("description", "Record id in the legacy system; used to upsert migrated data."),
            ("externalId", True),
            ("label", "Legacy Id"),
            ("length", 255),
            ("required", False),
            ("type", "Text"),
            ("unique", True),
        ],
    )


def _object_xml(obj: SfObject) -> str:
    e = obj.entity
    name_field: Spec = [("label", label(e.name_field_label or f"{e.label} Name"))]
    if e.name_is_auto_number:
        name_field += [
            ("displayFormat", e.name_auto_number_format or f"{api_base(e.key)[:3].upper()}-{{0000}}"),
            ("type", "AutoNumber"),
        ]
    else:
        name_field += [("type", "Text")]
    return render(
        "CustomObject",
        [
            ("deploymentStatus", "Deployed"),
            ("description", (e.description or "")[:1000] or None),
            ("enableActivities", True),
            ("enableBulkApi", True),
            ("enableFeeds", False),
            ("enableHistory", e.track_history),
            ("enableReports", True),
            ("enableSearch", True),
            ("enableSharing", True),
            ("enableStreamingApi", True),
            ("label", label(e.label)),
            ("nameField", name_field),
            ("pluralLabel", label(e.plural_label)),
            ("sharingModel", obj.sharing_model),
        ],
    )


def _layout_xml(obj: SfObject) -> str:
    items: list[Spec] = [
        [("behavior", "Readonly" if obj.entity.name_is_auto_number else "Required"), ("field", "Name")]
    ]
    for sf in obj.custom_fields:
        behavior = "Edit"
        if sf.sf_type == "AutoNumber":
            behavior = "Readonly"
        elif sf.required or sf.is_master_detail:
            behavior = "Required"
        items.append([("behavior", behavior), ("field", sf.api_name)])
    if obj.legacy_id_field and obj.legacy_id_field not in {sf.api_name for sf in obj.custom_fields}:
        items.append([("behavior", "Edit"), ("field", obj.legacy_id_field)])
    if obj.has_owner:
        items.append([("behavior", "Edit"), ("field", "OwnerId")])
    left, right = items[::2], items[1::2]

    def column(col: list[Spec]) -> Spec:
        return [("layoutItems", i) for i in col]

    system_items = (
        [[("behavior", "Readonly"), ("field", "CreatedById")]],
        [[("behavior", "Readonly"), ("field", "LastModifiedById")]],
    )
    return render(
        "Layout",
        [
            (
                "layoutSections",
                [
                    ("customLabel", False),
                    ("detailHeading", False),
                    ("editHeading", True),
                    ("label", "Information"),
                    ("layoutColumns", column(left)),
                    ("layoutColumns", column(right)),
                    ("style", "TwoColumnsTopToBottom"),
                ],
            ),
            (
                "layoutSections",
                [
                    ("customLabel", False),
                    ("detailHeading", True),
                    ("editHeading", False),
                    ("label", "System Information"),
                    ("layoutColumns", column(system_items[0])),
                    ("layoutColumns", column(system_items[1])),
                    ("style", "TwoColumnsTopToBottom"),
                ],
            ),
            ("showEmailCheckbox", False),
            ("showHighlightsPanel", False),
            ("showInteractionLogPanel", False),
            ("showRunAssignmentRulesCheckbox", False),
            ("showSubmitAndAttachButton", False),
        ],
    )


def _value_set_xml(process: Process, value_set: str, warnings: list[str]) -> str:
    stages = process.stages
    values: list[tuple[str, Spec]] = []
    has_terminal = False
    for i, s in enumerate(stages):
        spec: Spec = [("fullName", s.label[:255]), ("default", i == 0), ("label", s.label[:255])]
        if value_set == "OpportunityStage":
            closed = s.category != "open"
            won = s.category == "won"
            has_terminal |= won
            probability = s.probability if s.probability is not None else (100 if won else 0 if closed else 10)
            spec += [
                ("closed", closed),
                ("forecastCategory", FORECAST.get(s.category, "Pipeline")),
                ("probability", max(0, min(100, probability))),
                ("won", won),
            ]
        elif value_set == "LeadStatus":
            converted = s.category == "won"
            has_terminal |= converted
            spec += [("converted", converted)]
        elif value_set == "CaseStatus":
            closed = s.category != "open"
            has_terminal |= closed
            spec += [("closed", closed)]
        values.append(("standardValue", spec))
    if not has_terminal:
        need = {"OpportunityStage": "won", "LeadStatus": "converted", "CaseStatus": "closed"}[value_set]
        warnings.append(f"{value_set}: no stage is marked {need}; Salesforce requires at least one.")
    return render("StandardValueSet", [("sorted", False)] + values)


def _permission_set_xml(model: BusinessModel, plan: SfPlan, role) -> str:
    object_perms: list[Spec] = []
    field_perms: list[Spec] = []
    tabs: list[Spec] = []
    for p in sorted(role.permissions, key=lambda p: p.entity):
        obj = plan.objects.get(p.entity)
        if not obj:
            continue
        # Task/Event access comes from user permissions, not object permissions.
        if obj.api_name not in ("Task", "Event"):
            object_perms.append(
                [
                    ("allowCreate", p.create),
                    ("allowDelete", p.delete),
                    ("allowEdit", p.edit),
                    ("allowRead", p.read),
                    ("modifyAllRecords", p.modify_all),
                    ("object", obj.api_name),
                    ("viewAllRecords", p.view_all),
                ]
            )
        if not obj.standard and p.read:
            tabs.append([("tab", obj.api_name), ("visibility", "Visible")])
        fields = [(sf.api_name, sf) for sf in obj.custom_fields]
        if obj.legacy_id_field and obj.legacy_id_field not in {a for a, _ in fields}:
            fields.append((obj.legacy_id_field, None))
        for api, sf in fields:
            # Required and master-detail fields are always accessible and must not appear in FLS.
            if sf is not None and (sf.required or sf.is_master_detail):
                continue
            editable = p.edit and not (sf is not None and sf.sf_type == "AutoNumber")
            field_perms.append([("editable", editable), ("field", f"{obj.field_host}.{api}"), ("readable", p.read)])
    field_perms.sort(key=lambda s: s[1][1])
    spec: Spec = [("description", (role.description or f"Access for {role.label}")[:255])]
    spec += [("fieldPermissions", f) for f in field_perms]
    spec += [("hasActivationRequired", False), ("label", label(role.label, 80))]
    spec += [("objectPermissions", o) for o in object_perms]
    spec += [("tabSettings", t) for t in tabs]
    return render("PermissionSet", spec)


def _data_templates(model: BusinessModel, plan: SfPlan) -> tuple[dict[str, str], list[str]]:
    """CSV import templates in dependency order plus a migration plan."""
    deps: dict[str, set[str]] = defaultdict(set)
    for key, obj in plan.objects.items():
        for sf in obj.fields.values():
            parent = next((k for k, o in plan.objects.items() if o.api_name == sf.reference_to), None)
            if parent and parent != key:
                deps[key].add(parent)
    order: list[str] = []
    visiting: set[str] = set()

    def visit(k: str):
        if k in order or k in visiting:
            return
        visiting.add(k)
        for d in sorted(deps[k]):
            visit(d)
        visiting.discard(k)
        order.append(k)

    for k in plan.objects:
        visit(k)

    mappings = defaultdict(list)
    for m in model.data_mappings:
        mappings[m.entity].append(m)

    files: dict[str, str] = {}
    lines = [
        "# Data migration plan",
        "",
        "Load files in this order (parents before children). Use the",
        "Legacy Id external-id field with **upsert** so reloads never create duplicates.",
        "",
    ]
    step = 0
    for k in order:
        obj = plan.objects[k]
        if obj.api_name in ("Task", "Event") and k not in mappings:
            continue
        step += 1
        headers = []
        if obj.legacy_id_field:
            headers.append(obj.legacy_id_field)
        if not obj.standard or obj.api_name not in std.COMPOUND_NAME:
            if not obj.entity.name_is_auto_number:
                headers.append("Name")
        else:
            headers += std.COMPOUND_NAME[obj.api_name]
        for sf in obj.fields.values():
            if sf.api_name in headers or (sf.sf_type == "AutoNumber"):
                continue
            if sf.reference_to:
                parent = next((o for o in plan.objects.values() if o.api_name == sf.reference_to), None)
                rel = sf.relationship_name + "__r" if not sf.standard else sf.api_name.removesuffix("Id")
                if parent and parent.legacy_id_field:
                    headers.append(f"{rel}.{parent.legacy_id_field}")
                    continue
            headers.append(sf.api_name)
        buf = io.StringIO()
        csv.writer(buf).writerow(headers)
        fname = f"data/{step:02d}_{obj.api_name}.csv"
        files[fname] = buf.getvalue()
        lines.append(f"## {step}. {obj.entity.label} -> `{obj.api_name}` (`{fname}`)")
        if mappings.get(k):
            lines += ["", "| Source | Column | Salesforce field | Transform |", "|---|---|---|---|"]
            for m in mappings[k]:
                api = plan.field_api(k, m.field) or f"(unmapped: {m.field})"
                lines.append(f"| {m.source} | {m.column} | `{api}` | {m.transform or ''} |")
        else:
            lines.append("\nNo source data mapped.")
        lines.append("")
    lines += [
        "Suggested command per file:",
        "",
        "```",
        "sf data upsert bulk --sobject <Object> --file <file.csv> --external-id <Legacy_Id__c> "
        "--target-org <alias> --wait 10",
        "```",
        "",
    ]
    files["data/MIGRATION_PLAN.md"] = "\n".join(lines)
    return files, [plan.objects[k].api_name for k in order]


class SalesforceAdapter(PlatformAdapter):
    key = "salesforce"
    label = "Salesforce"
    description = "SFDX source project: objects, fields, layouts, permission sets, roles, flows, migration kit."
    automation_guidance = (
        "The target is Salesforce. These are generated automatically as Flows, so prefer them: triggers "
        "record_created / record_updated / record_created_or_updated with field conditions, and actions "
        "update_field, create_task, send_email to 'owner' or to an email field on the same record, and "
        "create_record for custom entities that link back to the triggering record. Scheduled or date-relative "
        "triggers, assign_owner, notify_user and call_webhook need manual setup: use them only when the "
        "business clearly needs them."
    )

    def generate(self, model: BusinessModel, project_name: str) -> ExportArtifact:
        api_version = get_settings().salesforce_api_version
        plan = build_plan(model)
        art = ExportArtifact(warnings=list(plan.warnings), manual_steps=list(plan.manual_steps))
        manifest: dict[str, list[str]] = defaultdict(list)
        objects_report = []

        for i, obj in enumerate(plan.objects.values()):
            base = f"{SRC}/objects/{obj.field_host}"
            if not obj.standard:
                art.files[f"{base}/{obj.api_name}.object-meta.xml"] = _object_xml(obj)
                manifest["CustomObject"].append(obj.api_name)
                layout_name = f"{obj.api_name}-{label(obj.entity.label)} Layout"
                art.files[f"{SRC}/layouts/{layout_name}.layout-meta.xml"] = _layout_xml(obj)
                manifest["Layout"].append(layout_name)
                art.files[f"{SRC}/tabs/{obj.api_name}.tab-meta.xml"] = render(
                    "CustomTab", [("customObject", True), ("motif", TAB_MOTIFS[i % len(TAB_MOTIFS)])]
                )
                manifest["CustomTab"].append(obj.api_name)
            for sf in obj.custom_fields:
                art.files[f"{base}/fields/{sf.api_name}.field-meta.xml"] = _field_xml(obj, sf)
                manifest["CustomField"].append(f"{obj.field_host}.{sf.api_name}")
            if obj.legacy_id_field and obj.legacy_id_field not in {sf.api_name for sf in obj.custom_fields}:
                art.files[f"{base}/fields/{obj.legacy_id_field}.field-meta.xml"] = _legacy_field_xml(
                    obj.legacy_id_field
                )
                manifest["CustomField"].append(f"{obj.field_host}.{obj.legacy_id_field}")
            if obj.standard and obj.custom_fields:
                names = ", ".join(sf.api_name for sf in obj.custom_fields)
                art.manual_steps.append(f"Add new fields to the {obj.api_name} page layouts: {names}.")
            objects_report.append(
                {
                    "entity": obj.entity.key,
                    "label": obj.entity.label,
                    "api_name": obj.api_name,
                    "standard": obj.standard,
                    "sharing_model": None if obj.standard else obj.sharing_model,
                    "fields": [
                        {"key": sf.key, "api_name": sf.api_name, "standard": sf.standard, "type": sf.sf_type}
                        for sf in obj.fields.values()
                    ],
                }
            )

        # Standard lifecycle value sets
        for p in model.processes:
            obj = plan.objects.get(p.entity)
            if not obj or obj.api_name not in std.STAGE_VALUE_SETS:
                continue
            stage_api, value_set = std.STAGE_VALUE_SETS[obj.api_name]
            sf = obj.fields.get(p.stage_field)
            if sf is None or sf.api_name != stage_api:
                continue
            if value_set in manifest["StandardValueSet"]:
                art.warnings.append(
                    f"Process '{p.name}': {value_set} already defined by another process; "
                    "use record types with sales/support processes."
                )
                continue
            art.files[f"{SRC}/standardValueSets/{value_set}.standardValueSet-meta.xml"] = _value_set_xml(
                p, value_set, art.warnings
            )
            manifest["StandardValueSet"].append(value_set)
            art.warnings.append(
                f"{value_set} will be replaced with the {len(p.stages)} designed stages; "
                "values in use by existing records must be kept or migrated first."
            )
        for p in model.processes:
            obj = plan.objects.get(p.entity)
            if obj and obj.api_name == "Opportunity" and p.stage_field in obj.fields:
                art.manual_steps.append(
                    f"Verify the Sales Process '{p.name}' includes the new stages "
                    "(Setup > Sales Processes) if the org uses opportunity record types."
                )

        # Roles (hierarchy) + permission sets (access)
        role_names = NameRegistry()
        role_api = {r.key: role_names.claim(api_base(r.key, 80), max_len=80) for r in model.roles}
        perm_names = NameRegistry()
        for r in model.roles:
            art.files[f"{SRC}/roles/{role_api[r.key]}.role-meta.xml"] = render(
                "Role",
                [
                    ("caseAccessLevel", "Edit"),
                    ("contactAccessLevel", "Edit"),
                    ("description", (r.description or "")[:255] or None),
                    ("mayForecastManagerShare", False),
                    ("name", label(r.label, 80)),
                    ("opportunityAccessLevel", "Edit"),
                    ("parentRole", role_api.get(r.reports_to) if r.reports_to else None),
                ],
            )
            manifest["Role"].append(role_api[r.key])
            ps = perm_names.claim(api_base(f"{r.key}_access", 80), max_len=80)
            art.files[f"{SRC}/permissionsets/{ps}.permissionset-meta.xml"] = _permission_set_xml(model, plan, r)
            manifest["PermissionSet"].append(ps)
        if model.roles:
            art.manual_steps.append("Assign users to roles and permission sets after deployment.")

        # Flows
        flows_report = []
        for a in model.automations:
            result = build_flow(plan, a, api_version)
            art.warnings += result.warnings
            art.manual_steps += result.manual_steps
            if result.xml and result.api_name:
                art.files[f"{SRC}/flows/{result.api_name}.flow-meta.xml"] = result.xml
                manifest["Flow"].append(result.api_name)
                flows_report.append({"automation": a.key, "flow": result.api_name, "actions": result.generated_actions})
        if flows_report:
            art.manual_steps.append("Flows are deployed as Draft: review, test and activate each one.")

        for i in model.integrations:
            art.manual_steps.append(f"Integration with {i.system} ({i.direction}, {i.frequency}): {i.description}")
        for rep in model.reports:
            obj = plan.objects.get(rep.entity)
            if obj:
                art.manual_steps.append(
                    f"Create report '{rep.name}' on {obj.api_name}"
                    + (f" grouped by {', '.join(rep.group_by)}" if rep.group_by else "")
                    + "."
                )

        data_files, load_order = _data_templates(model, plan)
        art.files.update(data_files)

        art.files["sfdx-project.json"] = (
            json.dumps(
                {
                    "packageDirectories": [{"path": "force-app", "default": True}],
                    "name": api_base(project_name, 80),
                    "namespace": "",
                    "sfdcLoginUrl": "https://login.salesforce.com",
                    "sourceApiVersion": api_version,
                },
                indent=2,
            )
            + "\n"
        )
        types = [
            ("types", [("members", m) for m in sorted(members)] + [("name", t)])
            for t, members in sorted(manifest.items())
            if members
        ]
        art.files["manifest/package.xml"] = render("Package", types + [("version", api_version)])
        art.files["README.md"] = self._readme(project_name, art)
        art.details = {
            "platform": self.key,
            "objects": objects_report,
            "flows": flows_report,
            "load_order": load_order,
            "counts": {t: len(m) for t, m in manifest.items()},
        }
        return art

    def automation_status(self, model: BusinessModel) -> dict[str, dict]:
        api_version = get_settings().salesforce_api_version
        plan = build_plan(model)
        out: dict[str, dict] = {}
        for a in model.automations:
            result = build_flow(plan, a, api_version)
            if result.xml and not result.manual_steps:
                out[a.key] = {"status": "flow", "note": "Flow · draft"}
                continue
            types = {x.type for x in a.actions}
            if a.trigger.type == "scheduled" or a.trigger.relative_date_field:
                reason = "scheduled path"
            elif a.trigger.type == "record_deleted":
                reason = "before-delete flow"
            elif "assign_owner" in types:
                reason = "assignment rule"
            elif "notify_user" in types:
                reason = "custom notification"
            elif "call_webhook" in types:
                reason = "HTTP callout"
            elif "send_email" in types:
                reason = "pick email recipient"
            elif "create_record" in types:
                reason = "create records step"
            else:
                reason = "manual setup"
            status = "partial" if result.xml else "manual"
            out[a.key] = {"status": status, "note": f"{'Flow + manual' if result.xml else 'Manual'} · {reason}"}
        return out

    @staticmethod
    def _readme(project_name: str, art: ExportArtifact) -> str:
        lines = [
            f"# {project_name} - Salesforce implementation",
            "",
            "Generated by CRM Architect. Review before deploying to production.",
            "",
            "## Deploy",
            "",
            "```bash",
            "sf org login web --alias target",
            "sf project deploy validate --source-dir force-app --target-org target   # check only",
            "sf project deploy start --source-dir force-app --target-org target",
            "```",
            "",
            "Always deploy to a sandbox first.",
            "",
        ]
        if art.warnings:
            lines += ["## Warnings", ""] + [f"- {w}" for w in art.warnings] + [""]
        if art.manual_steps:
            lines += ["## Manual steps", ""] + [f"- [ ] {s}" for s in art.manual_steps] + [""]
        lines += ["## Data migration", "", "See `data/MIGRATION_PLAN.md`.", ""]
        return "\n".join(lines)
