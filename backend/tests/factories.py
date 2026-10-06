from app.domain.business_model import BusinessModel


def solar_model() -> BusinessModel:
    """A realistic model exercising standard + custom objects, processes, automations and roles."""
    prov = {"source": "client_input", "reference": None, "confidence": 0.8}
    return BusinessModel.model_validate(
        {
            "profile": {
                "name": "SunPeak Solar",
                "industry": "Renewable energy",
                "description": "Residential solar installer.",
                "sales_model": "b2c",
                "revenue_models": ["project"],
                "regions": ["India"],
                "currencies": ["INR"],
            },
            "entities": [
                {
                    "key": "customer",
                    "label": "Customer",
                    "plural_label": "Customers",
                    "kind": "account",
                    "provenance": prov,
                    "fields": [
                        {"key": "phone", "label": "Phone", "type": "phone", "provenance": prov},
                        {"key": "city", "label": "City", "type": "text", "provenance": prov},
                        {
                            "key": "gst_number",
                            "label": "GST Number",
                            "type": "text",
                            "unique": True,
                            "provenance": prov,
                        },
                    ],
                },
                {
                    "key": "contact_person",
                    "label": "Contact Person",
                    "plural_label": "Contact People",
                    "kind": "contact",
                    "provenance": prov,
                    "fields": [
                        {"key": "email", "label": "Email", "type": "email", "pii": True, "provenance": prov},
                        {
                            "key": "customer",
                            "label": "Customer",
                            "type": "lookup",
                            "reference_entity": "customer",
                            "provenance": prov,
                        },
                    ],
                },
                {
                    "key": "deal",
                    "label": "Deal",
                    "plural_label": "Deals",
                    "kind": "opportunity",
                    "provenance": prov,
                    "fields": [
                        {
                            "key": "stage",
                            "label": "Stage",
                            "type": "picklist",
                            "options": [{"value": "x", "label": "x"}],
                            "provenance": prov,
                        },
                        {
                            "key": "system_size_kw",
                            "label": "System Size (kW)",
                            "type": "number",
                            "scale": 2,
                            "provenance": prov,
                        },
                        {
                            "key": "customer",
                            "label": "Customer",
                            "type": "lookup",
                            "reference_entity": "customer",
                            "provenance": prov,
                        },
                        {
                            "key": "roof_type",
                            "label": "Roof Type",
                            "type": "picklist",
                            "required": True,
                            "options": [{"value": "RCC", "label": "RCC"}, {"value": "Metal", "label": "Metal Sheet"}],
                            "provenance": prov,
                        },
                    ],
                },
                {
                    "key": "site_visit",
                    "label": "Site Visit",
                    "plural_label": "Site Visits",
                    "kind": "custom",
                    "name_is_auto_number": True,
                    "name_auto_number_format": "SV-{0000}",
                    "provenance": prov,
                    "fields": [
                        {
                            "key": "deal",
                            "label": "Deal",
                            "type": "lookup",
                            "reference_entity": "deal",
                            "relationship": "master_detail",
                            "provenance": prov,
                        },
                        {
                            "key": "visit_date",
                            "label": "Visit Date",
                            "type": "date",
                            "required": True,
                            "provenance": prov,
                        },
                        {
                            "key": "status",
                            "label": "Status",
                            "type": "picklist",
                            "provenance": prov,
                            "options": [
                                {"value": "Scheduled", "label": "Scheduled", "is_default": True},
                                {"value": "Completed", "label": "Completed"},
                            ],
                        },
                        {"key": "notes", "label": "Notes", "type": "long_text", "provenance": prov},
                        {"key": "roof_ok", "label": "Roof OK", "type": "boolean", "provenance": prov},
                        {"key": "engineer_email", "label": "Engineer Email", "type": "email", "provenance": prov},
                    ],
                },
                {
                    "key": "installation",
                    "label": "Installation",
                    "plural_label": "Installations",
                    "kind": "custom",
                    "provenance": prov,
                    "fields": [
                        {
                            "key": "deal",
                            "label": "Deal",
                            "type": "lookup",
                            "reference_entity": "deal",
                            "provenance": prov,
                        },
                        {"key": "phone", "label": "Phone", "type": "phone", "unique": True, "provenance": prov},
                    ],
                },
            ],
            "processes": [
                {
                    "key": "sales",
                    "name": "Solar sales",
                    "entity": "deal",
                    "stage_field": "stage",
                    "provenance": prov,
                    "stages": [
                        {"key": "new", "label": "New Enquiry", "probability": 10},
                        {"key": "visit", "label": "Site Visit Done", "probability": 40},
                        {"key": "won", "label": "Won", "category": "won", "probability": 100},
                        {"key": "lost", "label": "Lost", "category": "lost", "probability": 0},
                    ],
                },
            ],
            "automations": [
                {
                    "key": "visit_follow_up",
                    "name": "Follow up after site visit",
                    "entity": "site_visit",
                    "description": "When a visit completes, create a task and email the engineer.",
                    "provenance": prov,
                    "trigger": {"type": "record_updated"},
                    "conditions": [{"field": "status", "operator": "equals", "value": "Completed"}],
                    "actions": [
                        {"type": "create_task", "description": "Call customer with quote", "due_in_days": 2},
                        {
                            "type": "send_email",
                            "description": "Email engineer",
                            "recipient": "engineer_email",
                            "subject": "Visit completed",
                        },
                        {"type": "notify_user", "description": "Notify sales manager"},
                    ],
                },
                {
                    "key": "default_status",
                    "name": "Default visit status",
                    "entity": "site_visit",
                    "description": "New visits start as Scheduled.",
                    "provenance": prov,
                    "trigger": {"type": "record_created"},
                    "conditions": [{"field": "status", "operator": "is_blank"}],
                    "actions": [
                        {
                            "type": "update_field",
                            "description": "Set status",
                            "target_field": "status",
                            "value": "Scheduled",
                        }
                    ],
                },
                {
                    "key": "install_reminder",
                    "name": "Installation reminder",
                    "entity": "installation",
                    "description": "Remind 1 day before.",
                    "provenance": prov,
                    "trigger": {"type": "scheduled", "schedule": "daily"},
                    "actions": [{"type": "create_task", "description": "Remind crew"}],
                },
            ],
            "roles": [
                {
                    "key": "sales_manager",
                    "label": "Sales Manager",
                    "provenance": prov,
                    "permissions": [
                        {"entity": "deal", "read": True, "create": True, "edit": True, "view_all": True},
                        {"entity": "site_visit", "read": True, "edit": True},
                        {"entity": "customer", "read": True, "create": True, "edit": True},
                    ],
                },
                {
                    "key": "sales_rep",
                    "label": "Sales Rep",
                    "reports_to": "sales_manager",
                    "provenance": prov,
                    "permissions": [
                        {"entity": "deal", "read": True, "create": True, "edit": True},
                        {"entity": "site_visit", "read": True, "create": True, "edit": True},
                    ],
                },
            ],
            "data_mappings": [
                {"source": "leads.xlsx > Sheet1", "column": "Client", "entity": "customer", "field": "name"},
                {
                    "source": "leads.xlsx > Sheet1",
                    "column": "Mobile",
                    "entity": "customer",
                    "field": "phone",
                    "transform": "normalise to +91",
                },
                {"source": "visits.csv > Sheet1", "column": "Date", "entity": "site_visit", "field": "visit_date"},
            ],
        }
    )
