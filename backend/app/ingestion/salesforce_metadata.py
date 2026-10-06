"""Turn a Salesforce source/Metadata API ZIP into bounded, reviewable object facts."""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict

MAX_FILES = 10_000
MAX_XML_BYTES = 1_000_000
MAX_TOTAL_BYTES = 100_000_000
MAX_OBJECTS = 300
MAX_FIELDS = 300
MAX_RULES = 100

SUPPORTED_PATH = re.compile(
    r"(?:^|/)(?:objects/(?:[^/]+/)?[^/]+\.object(?:-meta\.xml)?"
    r"|objects/[^/]+/(?:fields/[^/]+\.field-meta\.xml"
    r"|recordTypes/[^/]+\.recordType-meta\.xml"
    r"|validationRules/[^/]+\.validationRule-meta\.xml)"
    r"|flows/[^/]+\.flow(?:-meta\.xml)?"
    r"|(?:reports/(?:[^/]+/)?[^/]+\.report|dashboards/(?:[^/]+/)?[^/]+\.dashboard|reportTypes/[^/]+\.reportType"
    r"|namedCredentials/[^/]+\.namedCredential|externalCredentials/[^/]+\.externalCredential"
    r"|remoteSiteSettings/[^/]+\.remoteSite|authproviders/[^/]+\.authprovider"
    r"|permissionsets/[^/]+\.permissionset|profiles/[^/]+\.profile"
    r"|layouts/[^/]+\.layout|flexipages/[^/]+\.flexipage|quickActions/[^/]+\.quickAction"
    r"|applications/[^/]+\.app|tabs/[^/]+\.tab|classes/[^/]+\.cls-meta\.xml"
    r"|triggers/[^/]+\.trigger-meta\.xml|settings/[^/]+\.settings"
    r"|flowDefinitions/[^/]+\.flowDefinition|duplicateRules/[^/]+\.duplicateRule"
    r"|assignmentRules/[^/]+\.assignmentRules|autoResponseRules/[^/]+\.autoResponseRules"
    r"|escalationRules/[^/]+\.escalationRules|workflowRules/[^/]+\.workflow"
    r"|approvalProcesses/[^/]+\.approvalProcess|sharingRules/[^/]+\.sharingRules"
    r"|customMetadata/[^/]+\.md)(?:-meta\.xml)?)$"
)

CATEGORY_PATH = re.compile(
    r"(?:^|/)(?P<folder>reports|dashboards|reportTypes|namedCredentials|externalCredentials|"
    r"remoteSiteSettings|authproviders|permissionsets|profiles|layouts|flexipages|quickActions|"
    r"applications|tabs|classes|triggers|settings|flowDefinitions|duplicateRules|assignmentRules|"
    r"autoResponseRules|escalationRules|workflowRules|approvalProcesses|sharingRules|customMetadata)/"
    r"(?:[^/]+/)?(?P<name>[^/]+)$"
)
CATEGORY_INFO = {
    "flows": ("Automations", "automation"),
    "reports": ("Reports", "reporting"),
    "dashboards": ("Dashboards", "reporting"),
    "reportTypes": ("Report types", "reporting"),
    "namedCredentials": ("Named credentials", "integration"),
    "externalCredentials": ("External credentials", "integration"),
    "remoteSiteSettings": ("Remote sites", "integration"),
    "authproviders": ("Auth providers", "integration"),
    "permissionsets": ("Permission sets", "security"),
    "profiles": ("Profiles", "security"),
    "layouts": ("Layouts", "experience"),
    "flexipages": ("Lightning pages", "experience"),
    "quickActions": ("Quick actions", "experience"),
    "applications": ("Applications", "experience"),
    "tabs": ("Tabs", "experience"),
    "classes": ("Apex classes", "code"),
    "triggers": ("Apex triggers", "code"),
    "settings": ("Org settings", "settings"),
    "flowDefinitions": ("Flow definitions", "automation"),
    "duplicateRules": ("Duplicate rules", "automation"),
    "assignmentRules": ("Assignment rules", "automation"),
    "autoResponseRules": ("Auto-response rules", "automation"),
    "escalationRules": ("Escalation rules", "automation"),
    "workflowRules": ("Workflow rules", "automation"),
    "approvalProcesses": ("Approval processes", "automation"),
    "sharingRules": ("Sharing rules", "security"),
    "customMetadata": ("Custom metadata records", "settings"),
}
SAFE_TAGS = {
    "flows": ("label", "processType", "status", "description"),
    "reports": ("name", "reportType", "format", "description"),
    "dashboards": ("title", "dashboardType", "description"),
    "reportTypes": ("label", "baseObject", "category", "description"),
    "namedCredentials": ("label", "namedCredentialType", "calloutStatus"),
    "externalCredentials": ("label", "authenticationProtocol"),
    "remoteSiteSettings": ("isActive",),
    "authproviders": ("friendlyName", "providerType"),
    "permissionsets": ("label",), "profiles": ("userLicense",),
    "layouts": (), "flexipages": ("masterLabel", "type"),
    "quickActions": ("label", "type", "targetObject"),
    "applications": ("label",), "tabs": ("label",),
    "classes": ("status",), "triggers": ("status",), "settings": (),
    "flowDefinitions": ("activeVersionNumber",),
    "duplicateRules": ("masterLabel", "isActive"),
    "assignmentRules": (), "autoResponseRules": (), "escalationRules": (),
    "workflowRules": (), "approvalProcesses": ("label", "active"),
    "sharingRules": (), "customMetadata": ("label",),
}


def is_supported_path(path: str) -> bool:
    return bool(SUPPORTED_PATH.search(path.replace("\\", "/")))


def _child(element: ET.Element, name: str) -> str | None:
    node = element.find(f"{{*}}{name}")
    return (node.text or "").strip() or None if node is not None else None


def _field(element: ET.Element) -> dict:
    values = []
    for value in element.findall("./{*}valueSet/{*}valueSetDefinition/{*}value")[:30]:
        label = _child(value, "label") or _child(value, "fullName")
        if label:
            values.append(label)
    return {
        "api_name": _child(element, "fullName"),
        "label": _child(element, "label"),
        "type": _child(element, "type"),
        "description": _child(element, "description"),
        "reference_to": _child(element, "referenceTo"),
        "required": _child(element, "required") == "true",
        "external_id": _child(element, "externalId") == "true",
        "formula": bool(_child(element, "formula")),
        "values": values,
    }


def _record_type(element: ET.Element) -> dict:
    return {
        "api_name": _child(element, "fullName"),
        "label": _child(element, "label"),
        "active": _child(element, "active") == "true",
        "description": _child(element, "description"),
    }


def _validation_rule(element: ET.Element) -> dict:
    return {
        "api_name": _child(element, "fullName"),
        "active": _child(element, "active") == "true",
        "description": _child(element, "description"),
        "error_message": _child(element, "errorMessage"),
    }


def _blank_object(api_name: str) -> dict:
    return {
        "object_api_name": api_name,
        "label": api_name,
        "plural_label": None,
        "description": None,
        "sharing_model": None,
        "fields": [],
        "record_types": [],
        "validation_rules": [],
        "flows": [],
        "source_format": None,
    }


def _attach_unique(existing: list[dict], incoming: list[dict], limit: int) -> tuple[list[dict], bool]:
    seen = {item.get("api_name") for item in existing}
    result = list(existing)
    for item in incoming:
        if item.get("api_name") not in seen:
            result.append(item)
            seen.add(item.get("api_name"))
    return result[:limit], len(result) > limit


def _analyse(obj: dict) -> None:
    fields = obj["fields"]
    relationships = sorted({f["reference_to"] for f in fields if f.get("reference_to")})
    picklists = [f["label"] or f["api_name"] for f in fields if f["type"] in ("Picklist", "MultiselectPicklist")]
    lifecycle_candidates = [
        f["label"] or f["api_name"]
        for f in fields
        if f["type"] == "Picklist"
        and any(term in (f["api_name"] or "").lower() for term in ("stage", "status", "phase"))
    ]
    warnings = obj.pop("_warnings", [])
    if not obj["description"]:
        warnings.append("Object purpose is not documented in metadata; confirm what this record represents.")
    if not fields:
        warnings.append("No field definitions were retrieved for this object.")
    obj["analysis"] = {
        "field_count": len(fields),
        "relationship_targets": relationships,
        "picklist_fields": picklists,
        "lifecycle_candidates": lifecycle_candidates,
        "migration_keys": [f["api_name"] for f in fields if f["external_id"]],
        "computed_fields": [f["api_name"] for f in fields if f["formula"]],
        "record_type_count": len(obj["record_types"]),
        "validation_rule_count": len(obj["validation_rules"]),
        "record_triggered_flow_count": len(obj["flows"]),
        "warnings": warnings,
    }


def parse_salesforce_zip(data: bytes, *, allow_no_objects: bool = False) -> list[dict]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("Upload a valid Salesforce metadata ZIP") from exc
    with archive:
        files = archive.infolist()
        if len(files) > MAX_FILES or sum(f.file_size for f in files) > MAX_TOTAL_BYTES:
            raise ValueError("Metadata ZIP is too large to import")
        objects: dict[str, dict] = {}
        separate_fields: dict[str, list[dict]] = defaultdict(list)
        separate_record_types: dict[str, list[dict]] = defaultdict(list)
        separate_rules: dict[str, list[dict]] = defaultdict(list)
        flows: list[dict] = []
        for member in files:
            path = member.filename.replace("\\", "/")
            if member.is_dir():
                continue
            # Salesforce DX: objects/Thing__c/Thing__c.object-meta.xml and fields/*.field-meta.xml.
            # Metadata API: objects/Thing__c.object with embedded <fields> elements.
            object_match = re.search(r"(?:^|/)objects/(?:[^/]+/)?([^/]+)\.object(?:-meta\.xml)?$", path)
            field_match = re.search(r"(?:^|/)objects/([^/]+)/fields/([^/]+)\.field-meta\.xml$", path)
            record_type_match = re.search(r"(?:^|/)objects/([^/]+)/recordTypes/([^/]+)\.recordType-meta\.xml$", path)
            rule_match = re.search(r"(?:^|/)objects/([^/]+)/validationRules/([^/]+)\.validationRule-meta\.xml$", path)
            flow_match = re.search(r"(?:^|/)flows/([^/]+)\.flow(?:-meta\.xml)?$", path)
            if not any((object_match, field_match, record_type_match, rule_match, flow_match)):
                continue
            if member.file_size > MAX_XML_BYTES:
                raise ValueError(f"Metadata XML exceeds 1 MB: {path[:180]}")
            if member.flag_bits & 1:
                raise ValueError(f"Encrypted ZIP entry is not supported: {path[:180]}")
            try:
                xml = archive.read(member)
                if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                    raise ValueError(f"XML declarations are not supported: {path[:180]}")
                root = ET.fromstring(xml)
            except (ET.ParseError, RuntimeError) as exc:
                raise ValueError(f"Could not parse Salesforce XML: {path[:180]}") from exc
            if object_match:
                api_name = _child(root, "fullName") or object_match.group(1)
                obj = objects.setdefault(api_name, _blank_object(api_name))
                obj.update({
                    "label": _child(root, "label") or api_name,
                    "plural_label": _child(root, "pluralLabel"),
                    "description": _child(root, "description"),
                    "sharing_model": _child(root, "sharingModel"),
                    "source_format": "salesforce_dx" if path.endswith("-meta.xml") else "metadata_api",
                })
                for key, tag, parser, limit in (
                    ("fields", "fields", _field, MAX_FIELDS),
                    ("record_types", "recordTypes", _record_type, MAX_RULES),
                    ("validation_rules", "validationRules", _validation_rule, MAX_RULES),
                ):
                    parsed = [parser(node) for node in root.findall(f"./{{*}}{tag}")]
                    obj[key], clipped = _attach_unique(obj[key], parsed, limit)
                    if clipped:
                        note = f"Only the first {limit} {key.replace('_', ' ')} were kept."
                        obj.setdefault("_warnings", []).append(note)
            elif field_match:
                api_name = field_match.group(1)
                parsed = _field(root)
                parsed["api_name"] = parsed["api_name"] or field_match.group(2)
                separate_fields[api_name].append(parsed)
            elif record_type_match:
                parsed = _record_type(root)
                parsed["api_name"] = parsed["api_name"] or record_type_match.group(2)
                separate_record_types[record_type_match.group(1)].append(parsed)
            elif rule_match:
                parsed = _validation_rule(root)
                parsed["api_name"] = parsed["api_name"] or rule_match.group(2)
                separate_rules[rule_match.group(1)].append(parsed)
            elif flow_match:
                start = root.find("./{*}start")
                target = _child(start, "object") if start is not None else None
                trigger = _child(start, "triggerType") if start is not None else None
                if target and trigger and trigger.startswith("Record"):
                    flows.append({
                        "api_name": flow_match.group(1),
                        "label": _child(root, "label") or flow_match.group(1),
                        "object_api_name": target,
                        "trigger_type": trigger,
                        "status": _child(root, "status"),
                    })
        for api_name in set(separate_fields) | set(separate_record_types) | set(separate_rules):
            obj = objects.setdefault(api_name, _blank_object(api_name))
            for key, incoming, limit in (
                ("fields", separate_fields[api_name], MAX_FIELDS),
                ("record_types", separate_record_types[api_name], MAX_RULES),
                ("validation_rules", separate_rules[api_name], MAX_RULES),
            ):
                obj[key], clipped = _attach_unique(obj[key], incoming, limit)
                if clipped:
                    obj.setdefault("_warnings", []).append(f"Only the first {limit} {key.replace('_', ' ')} were kept.")
            obj["source_format"] = obj["source_format"] or "salesforce_dx"
        for flow in flows:
            target = flow["object_api_name"]
            if target in objects:
                objects[target]["flows"].append(flow)
        for obj in objects.values():
            _analyse(obj)
        if not objects and not allow_no_objects:
            raise ValueError("No Salesforce object metadata found in this ZIP")
        if len(objects) > MAX_OBJECTS:
            raise ValueError(f"Metadata ZIP has more than {MAX_OBJECTS} objects; split it into smaller imports")
        return sorted(objects.values(), key=lambda obj: obj["object_api_name"])


def parse_salesforce_bundle(data: bytes) -> tuple[list[dict], list[dict]]:
    """Return object facts and bounded category summaries; never retain credential secrets or raw XML."""
    objects = parse_salesforce_zip(data, allow_no_objects=True)
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            path = member.filename.replace("\\", "/")
            if member.is_dir() or not is_supported_path(path):
                continue
            flow = re.search(r"(?:^|/)flows/([^/]+)\.flow(?:-meta\.xml)?$", path)
            match = CATEGORY_PATH.search(path)
            if flow:
                folder, name = "flows", flow.group(1)
            elif match:
                folder, filename = match.group("folder"), match.group("name")
                suffix = re.search(r"\.[^.]+(?:-meta\.xml)?$", filename)
                name = filename[:suffix.start()] if suffix else filename
            else:
                continue
            if member.file_size > MAX_XML_BYTES or member.flag_bits & 1:
                raise ValueError(f"Unsupported metadata XML: {path[:180]}")
            xml = archive.read(member)
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise ValueError(f"XML declarations are not supported: {path[:180]}")
            try:
                root = ET.fromstring(xml)
            except ET.ParseError as exc:
                raise ValueError(f"Could not parse Salesforce XML: {path[:180]}") from exc
            item = {"api_name": name, "metadata_type": folder}
            if folder in ("reports", "dashboards"):
                item["folder"] = path.rsplit("/", 2)[-2] if path.count("/") >= 2 else ""
            for tag in SAFE_TAGS[folder]:
                value = _child(root, tag)
                if value:
                    item[tag] = value[:250]
            if folder == "flows":
                start = root.find("./{*}start")
                if start is not None:
                    for tag in ("object", "triggerType"):
                        value = _child(start, tag)
                        if value:
                            item[tag] = value[:120]
            elif folder == "reports":
                item["columns"] = [_child(node, "field") or (node.text or "").strip()
                                   for node in root.findall("./{*}columns")[:12]]
            elif folder == "dashboards":
                item["component_count"] = len(root.findall(".//{*}dashboardComponent"))
            elif folder in ("permissionsets", "profiles"):
                item["object_permission_count"] = len(root.findall("./{*}objectPermissions"))
                item["field_permission_count"] = len(root.findall("./{*}fieldPermissions"))
            elif folder == "layouts":
                item["section_count"] = len(root.findall("./{*}layoutSections"))
            # A selected project often contains both force-app and retrieve-output copies.
            # Keep one logical component, preserving report/dashboard folder identity.
            key = (item.get("folder", ""), name) if folder in ("reports", "dashboards") else name
            grouped[folder][key] = item
    categories = []
    for folder, items in grouped.items():
        label, area = CATEGORY_INFO[folder]
        values = sorted(items.values(), key=lambda item: item["api_name"])
        categories.append({"title": label, "area": area, "metadata_type": folder,
                           "count": len(values), "items": values})
    if not objects and not categories:
        raise ValueError("No supported Salesforce metadata found in this ZIP")
    return objects, categories


def summarize_category(category: dict) -> str:
    names = [item.get("label") or item.get("name") or item.get("title") or item["api_name"]
             for item in category["items"]]
    return (f"Salesforce {category['title'].lower()}: {category['count']} found. "
            + ", ".join(names[:35])
            + (f", and {len(names) - 35} more" if len(names) > 35 else ""))[:4000]


def summarize_object(obj: dict) -> str:
    header = f"Salesforce object {obj['label']} ({obj['object_api_name']})."
    if obj["description"]:
        header += f" Purpose: {obj['description'][:350]}."
    signals = obj["analysis"]
    if signals["relationship_targets"]:
        header += " Relationships: " + ", ".join(signals["relationship_targets"][:10]) + "."
    if obj["record_types"]:
        header += " Record types: " + ", ".join(r["label"] or r["api_name"] for r in obj["record_types"][:10]) + "."
    if signals["lifecycle_candidates"]:
        header += " Possible lifecycle fields: " + ", ".join(signals["lifecycle_candidates"][:6]) + "."
    if signals["migration_keys"]:
        header += " External IDs: " + ", ".join(signals["migration_keys"][:6]) + "."
    if obj["flows"]:
        header += " Record-triggered flows: " + ", ".join(f["label"] for f in obj["flows"][:10]) + "."
    details = []
    for field in obj["fields"]:
        description = f"{field['label'] or field['api_name']} ({field['api_name']}, {field['type'] or 'unknown'})"
        if field["reference_to"]:
            description += f" → {field['reference_to']}"
        if field["values"]:
            description += ": " + ", ".join(field["values"][:8])
        details.append(description)
    return (header + " Fields: " + "; ".join(details))[:4000]
