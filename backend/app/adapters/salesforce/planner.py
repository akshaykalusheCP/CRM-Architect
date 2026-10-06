"""Map the canonical model onto Salesforce objects and fields, applying platform rules and limits."""

from dataclasses import dataclass, field

from app.adapters.salesforce import standard as std
from app.adapters.salesforce.naming import NameRegistry, api_base
from app.domain.business_model import BusinessModel, Entity, Field_, FieldType

UNIQUE_CAPABLE = {FieldType.text, FieldType.email, FieldType.number}
MAX_MASTER_DETAIL = 2


@dataclass
class SfField:
    key: str
    api_name: str
    standard: bool
    canonical: Field_ | None = None
    sf_type: str | None = None  # None for standard fields
    reference_to: str | None = None
    relationship_name: str | None = None
    relationship_order: int | None = None
    required: bool = False

    @property
    def is_master_detail(self) -> bool:
        return self.sf_type == "MasterDetail"


@dataclass
class SfObject:
    entity: Entity
    api_name: str
    standard: bool
    fields: dict[str, SfField] = field(default_factory=dict)
    sharing_model: str = "ReadWrite"
    legacy_id_field: str | None = None
    _names: NameRegistry = field(default_factory=NameRegistry)

    @property
    def field_host(self) -> str:
        """Object that holds custom field metadata (Task/Event fields live on Activity)."""
        return std.FIELD_HOST.get(self.api_name, self.api_name)

    @property
    def has_owner(self) -> bool:
        return not any(f.is_master_detail for f in self.fields.values())

    @property
    def custom_fields(self) -> list[SfField]:
        return [f for f in self.fields.values() if not f.standard]


@dataclass
class SfPlan:
    objects: dict[str, SfObject]
    warnings: list[str]
    manual_steps: list[str]

    def field_api(self, entity_key: str, field_key: str) -> str | None:
        obj = self.objects.get(entity_key)
        if not obj:
            return None
        if field_key in obj.fields:
            return obj.fields[field_key].api_name
        return std.IMPLICIT_FIELDS.get(field_key)


def _custom_type(f: Field_, warnings: list[str], where: str) -> str:
    match f.type:
        case FieldType.text:
            if f.length and f.length > 255:
                warnings.append(f"{where}: text longer than 255 chars created as Long Text Area.")
                return "LongTextArea"
            return "Text"
        case FieldType.textarea:
            return "TextArea"
        case FieldType.long_text:
            return "LongTextArea"
        case FieldType.rich_text:
            return "Html"
        case FieldType.number:
            return "Number"
        case FieldType.currency:
            return "Currency"
        case FieldType.percent:
            return "Percent"
        case FieldType.date:
            return "Date"
        case FieldType.datetime:
            return "DateTime"
        case FieldType.boolean:
            return "Checkbox"
        case FieldType.picklist:
            return "Picklist"
        case FieldType.multi_picklist:
            return "MultiselectPicklist"
        case FieldType.email:
            return "Email"
        case FieldType.phone:
            return "Phone"
        case FieldType.url:
            return "Url"
        case FieldType.auto_number:
            return "AutoNumber"
        case FieldType.lookup:
            return "Lookup"
    return "Text"


def build_plan(model: BusinessModel) -> SfPlan:
    warnings: list[str] = []
    manual: list[str] = []
    objects: dict[str, SfObject] = {}
    object_names = NameRegistry(set(std.KIND_TO_OBJECT.values()))
    claimed: dict[str, str] = {}

    # 1. Objects
    for e in model.entities:
        target = std.KIND_TO_OBJECT.get(e.kind)
        if target and target not in claimed:
            claimed[target] = e.key
            objects[e.key] = SfObject(entity=e, api_name=target, standard=True)
            if target == "Quote":
                manual.append("Enable Quotes (Setup > Quote Settings) before deploying: Quote fields are included.")
            if target == "Order":
                manual.append("Enable Orders (Setup > Order Settings) before deploying: Order fields are included.")
        else:
            if target:
                warnings.append(
                    f"Entity '{e.label}' is another {e.kind}; '{objects[claimed[target]].entity.label}' already "
                    f"uses {target}. Created as a custom object. Consider {target} record types instead."
                )
            api = object_names.claim(api_base(e.key, 38), "__c")
            objects[e.key] = SfObject(entity=e, api_name=api, standard=False)

    stage_fields = {
        (p.entity, p.stage_field)
        for p in model.processes
        if p.entity in objects and objects[p.entity].api_name in std.STAGE_VALUE_SETS
    }
    parent_rel_names: dict[str, NameRegistry] = {}

    # 2. Fields
    for e in model.entities:
        obj = objects[e.key]
        std_fields = std.STANDARD_FIELDS.get(obj.api_name, {}) if obj.standard else {}
        std_lookups = dict(std.STANDARD_LOOKUPS.get(obj.api_name, {})) if obj.standard else {}
        used_std: set[str] = set()
        md_count = 0
        for f in e.fields:
            where = f"{e.label}.{f.label}"
            if f.key in std.IMPLICIT_FIELDS:
                continue
            if (e.key, f.key) in stage_fields:
                api = std.STAGE_VALUE_SETS[obj.api_name][0]
                obj.fields[f.key] = SfField(f.key, api, standard=True, canonical=f)
                used_std.add(api)
                continue
            if f.key in std_fields and std_fields[f.key] not in used_std:
                api = std_fields[f.key]
                used_std.add(api)
                obj.fields[f.key] = SfField(f.key, api, standard=True, canonical=f)
                if f.type in (FieldType.picklist, FieldType.multi_picklist) and f.options:
                    values = ", ".join(o.label for o in f.options)
                    manual.append(f"Review standard picklist {obj.api_name}.{api} values: {values}.")
                continue
            if f.type == FieldType.lookup and f.reference_entity in objects:
                parent = objects[f.reference_entity]
                std_api = std_lookups.get(parent.api_name) if parent.standard else None
                if std_api and std_api not in used_std:
                    used_std.add(std_api)
                    obj.fields[f.key] = SfField(
                        f.key, std_api, standard=True, canonical=f, reference_to=parent.api_name
                    )
                    continue
                if obj.api_name in ("Task", "Event"):
                    warnings.append(f"{where}: activities link to records through Related To/Name; lookup skipped.")
                    continue

            sf_type = _custom_type(f, warnings, where)
            api = obj._names.claim(api_base(f.key, 38), "__c")
            sf = SfField(f.key, api, standard=False, canonical=f, sf_type=sf_type, required=f.required)

            if sf_type == "Lookup":
                parent = objects.get(f.reference_entity or "")
                if not parent:
                    warnings.append(f"{where}: lookup target '{f.reference_entity}' not found; field skipped.")
                    continue
                sf.reference_to = parent.api_name
                if f.relationship == "master_detail":
                    reason = None
                    if obj.standard:
                        reason = f"{obj.api_name} is a standard object and cannot be a detail"
                    elif parent.standard and parent.api_name not in std.MASTER_CAPABLE:
                        reason = f"{parent.api_name} cannot be a master"
                    elif md_count >= MAX_MASTER_DETAIL:
                        reason = "an object can have at most two master-detail relationships"
                    if reason:
                        warnings.append(f"{where}: created as a lookup because {reason}.")
                    else:
                        sf.sf_type = "MasterDetail"
                        sf.relationship_order = md_count
                        md_count += 1
                registry = parent_rel_names.setdefault(parent.api_name, NameRegistry())
                sf.relationship_name = registry.claim(api_base(f"{e.plural_label}_{f.key}", 38))
            if f.unique and f.type not in UNIQUE_CAPABLE:
                warnings.append(f"{where}: Salesforce only enforces uniqueness on text/number/email; ignored.")
            if f.required and obj.api_name in ("Task", "Event"):
                sf.required = False
                warnings.append(f"{where}: required activity fields are enforced via page layout instead.")
            obj.fields[f.key] = sf

        # Object-level settings
        if not obj.standard:
            if md_count:
                obj.sharing_model = "ControlledByParent"
            elif any(r.reports_to for r in model.roles):
                obj.sharing_model = "Private"

    # 3. Legacy ids for migration upserts
    mapped_entities = {m.entity for m in model.data_mappings}
    for key in mapped_entities:
        obj = objects.get(key)
        if not obj or obj.api_name in ("Task", "Event"):
            continue
        if any(f.canonical and f.canonical.external_id for f in obj.custom_fields):
            obj.legacy_id_field = next(f.api_name for f in obj.custom_fields if f.canonical and f.canonical.external_id)
            continue
        obj.legacy_id_field = obj._names.claim("Legacy_Id", "__c")

    return SfPlan(objects=objects, warnings=warnings, manual_steps=manual)
