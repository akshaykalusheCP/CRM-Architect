"""Canonical, platform-neutral business model.

Every CRM adapter (Salesforce, Zoho, Odoo, ...) consumes this model. The LLM produces it,
consultants edit it, and the validator guards it. Keys are snake_case identifiers that are
stable across versions so that edits, questions and mappings can reference them.

The schema deliberately avoids free-form dicts and recursion so it can be used as a strict
JSON schema for LLM structured output.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class SourceKind(StrEnum):
    client_input = "client_input"
    file = "file"
    document = "document"
    answer = "answer"
    inferred = "inferred"
    best_practice = "best_practice"


class Provenance(_Model):
    source: SourceKind = Field(description="Where this element came from.")
    reference: str | None = Field(
        default=None,
        description="Pointer to the evidence, e.g. 'leads.xlsx > Sheet1 > Phone' or a question key.",
    )
    confidence: float = Field(default=0.7, description="0.0-1.0 confidence that this element is correct.")


class FieldType(StrEnum):
    text = "text"
    textarea = "textarea"
    long_text = "long_text"
    rich_text = "rich_text"
    number = "number"
    currency = "currency"
    percent = "percent"
    date = "date"
    datetime = "datetime"
    boolean = "boolean"
    picklist = "picklist"
    multi_picklist = "multi_picklist"
    email = "email"
    phone = "phone"
    url = "url"
    lookup = "lookup"
    auto_number = "auto_number"


class EntityKind(StrEnum):
    """Archetype of an entity. Adapters map archetypes to native objects where one exists."""

    account = "account"
    contact = "contact"
    lead = "lead"
    opportunity = "opportunity"
    case = "case"
    product = "product"
    quote = "quote"
    order = "order"
    contract = "contract"
    campaign = "campaign"
    task = "task"
    event = "event"
    custom = "custom"


class PicklistOption(_Model):
    value: str
    label: str
    is_default: bool = False


class Field_(_Model):
    key: str = Field(description="snake_case identifier, unique within the entity.")
    label: str
    type: FieldType
    description: str | None = None
    required: bool = False
    unique: bool = False
    external_id: bool = Field(default=False, description="True if used to match records during migration.")
    options: list[PicklistOption] = Field(default_factory=list, description="Only for picklist types.")
    reference_entity: str | None = Field(default=None, description="Entity key, only for lookup fields.")
    relationship: Literal["lookup", "master_detail"] | None = Field(
        default=None,
        description="Only for lookup fields. master_detail = child cannot exist without parent.",
    )
    length: int | None = Field(default=None, description="Max length for text fields.")
    scale: int | None = Field(default=None, description="Decimal places for number/currency/percent.")
    default_value: str | None = None
    auto_number_format: str | None = Field(default=None, description="e.g. 'INV-{0000}' for auto_number.")
    pii: bool = Field(default=False, description="Personal or sensitive data.")
    provenance: Provenance


class Entity(_Model):
    key: str = Field(description="snake_case identifier, singular, e.g. 'site_visit'.")
    label: str
    plural_label: str
    kind: EntityKind
    description: str | None = None
    name_field_label: str = Field(default="Name", description="Label of the record's primary name field.")
    name_is_auto_number: bool = False
    name_auto_number_format: str | None = None
    track_history: bool = False
    estimated_record_volume: int | None = None
    fields: list[Field_] = Field(default_factory=list)
    provenance: Provenance


class Stage(_Model):
    key: str
    label: str
    description: str | None = None
    category: Literal["open", "won", "lost", "closed"] = "open"
    probability: int | None = Field(default=None, description="0-100, sales pipelines only.")


class Process(_Model):
    key: str
    name: str
    entity: str = Field(description="Entity key the process runs on.")
    stage_field: str = Field(description="Picklist field key on the entity that holds the stage.")
    description: str | None = None
    stages: list[Stage]
    provenance: Provenance


class ConditionOperator(StrEnum):
    equals = "equals"
    not_equals = "not_equals"
    greater_than = "greater_than"
    less_than = "less_than"
    contains = "contains"
    is_blank = "is_blank"
    is_not_blank = "is_not_blank"
    changed = "changed"


class Condition(_Model):
    field: str = Field(description="Field key on the automation's entity.")
    operator: ConditionOperator
    value: str | None = None


class TriggerType(StrEnum):
    record_created = "record_created"
    record_updated = "record_updated"
    record_created_or_updated = "record_created_or_updated"
    record_deleted = "record_deleted"
    scheduled = "scheduled"


class Trigger(_Model):
    type: TriggerType
    schedule: str | None = Field(default=None, description="For scheduled triggers, e.g. 'daily 09:00'.")
    relative_date_field: str | None = Field(
        default=None, description="For time-based triggers relative to a date field on the record."
    )
    offset_days: int | None = Field(default=None, description="Days after (+) or before (-) the date field.")


class ActionType(StrEnum):
    update_field = "update_field"
    create_task = "create_task"
    send_email = "send_email"
    assign_owner = "assign_owner"
    notify_user = "notify_user"
    create_record = "create_record"
    call_webhook = "call_webhook"


class Action(_Model):
    type: ActionType
    description: str
    target_field: str | None = Field(default=None, description="update_field: field key to set.")
    value: str | None = Field(default=None, description="update_field: literal value to set.")
    subject: str | None = Field(default=None, description="create_task / send_email / notify_user subject.")
    body: str | None = Field(default=None, description="send_email / notify_user body.")
    recipient: str | None = Field(default=None, description="Role key, 'owner', or an email field key on the record.")
    related_entity: str | None = Field(default=None, description="create_record: entity key to create.")
    due_in_days: int | None = Field(default=None, description="create_task: due date offset.")
    assignment_rule: str | None = Field(
        default=None, description="assign_owner: e.g. 'round_robin:sales_rep' or 'territory by region'."
    )


class Automation(_Model):
    key: str
    name: str
    entity: str
    description: str
    trigger: Trigger
    conditions: list[Condition] = Field(default_factory=list)
    condition_logic: Literal["all", "any"] = "all"
    actions: list[Action]
    provenance: Provenance


class Permission(_Model):
    entity: str
    read: bool = True
    create: bool = False
    edit: bool = False
    delete: bool = False
    view_all: bool = False
    modify_all: bool = False


class Role(_Model):
    key: str
    label: str
    description: str | None = None
    reports_to: str | None = Field(default=None, description="Role key of the manager role.")
    estimated_users: int | None = None
    permissions: list[Permission] = Field(default_factory=list)
    provenance: Provenance


class Integration(_Model):
    key: str
    system: str = Field(description="External system, e.g. 'Tally', 'Shopify', 'WhatsApp Business'.")
    direction: Literal["inbound", "outbound", "bidirectional"]
    entities: list[str] = Field(default_factory=list)
    frequency: Literal["real_time", "hourly", "daily", "weekly", "on_demand"] = "daily"
    description: str
    provenance: Provenance


class Report(_Model):
    key: str
    name: str
    entity: str
    kind: Literal["tabular", "summary", "matrix", "metric", "chart"] = "summary"
    group_by: list[str] = Field(default_factory=list, description="Field keys on the entity.")
    metrics: list[str] = Field(default_factory=list, description="e.g. 'count', 'sum:amount'.")
    filters: str | None = None
    audience: list[str] = Field(default_factory=list, description="Role keys.")


class DataMapping(_Model):
    source: str = Field(description="Source file (and sheet) name, e.g. 'leads.xlsx > Sheet1'.")
    column: str
    entity: str
    field: str
    transform: str | None = Field(default=None, description="Cleansing/transform rule, e.g. 'split full name'.")


class Assumption(_Model):
    statement: str
    rationale: str | None = None
    confidence: float = 0.6
    related: list[str] = Field(default_factory=list, description="Keys of related entities/processes.")


class BusinessProfile(_Model):
    name: str
    industry: str
    sub_industry: str | None = None
    description: str
    sales_model: Literal["b2b", "b2c", "b2b2c", "mixed", "not_applicable"] = "b2b"
    revenue_models: list[
        Literal["one_time", "subscription", "project", "service_contract", "usage", "commission", "donation"]
    ] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    currencies: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    crm_user_count: int | None = None
    compliance: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(
        default_factory=list, description="Requests better served outside the CRM, e.g. inventory/accounting."
    )


class BusinessModel(_Model):
    schema_version: str = SCHEMA_VERSION
    profile: BusinessProfile
    entities: list[Entity] = Field(default_factory=list)
    processes: list[Process] = Field(default_factory=list)
    automations: list[Automation] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    integrations: list[Integration] = Field(default_factory=list)
    reports: list[Report] = Field(default_factory=list)
    data_mappings: list[DataMapping] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)

    def entity(self, key: str) -> Entity | None:
        return next((e for e in self.entities if e.key == key), None)


class QuestionCategory(StrEnum):
    business = "business"
    entity = "entity"
    field = "field"
    process = "process"
    automation = "automation"
    security = "security"
    integration = "integration"
    data = "data"
    scope = "scope"


class ClarifyingQuestion(_Model):
    key: str = Field(description="Stable snake_case key so the question is not re-asked.")
    question: str
    why: str = Field(description="What in the design depends on the answer.")
    category: QuestionCategory
    priority: Literal["high", "medium", "low"]
    suggested_answers: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)


class DiscoveryResult(_Model):
    summary: str = Field(description="Short narrative of the business and the proposed CRM design.")
    model: BusinessModel
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    changes: list[str] = Field(
        default_factory=list, description="When refining, what changed versus the previous model and why."
    )
