from app.adapters.base import PlatformAdapter
from app.adapters.salesforce.generator import SalesforceAdapter

_ADAPTERS: dict[str, PlatformAdapter] = {a.key: a for a in (SalesforceAdapter(),)}

# Platforms on the roadmap; shown in the UI but not yet exportable.
PLANNED = {"zoho": "Zoho CRM", "odoo": "Odoo", "hubspot": "HubSpot"}


def get_adapter(key: str) -> PlatformAdapter | None:
    return _ADAPTERS.get(key)


def list_platforms() -> list[dict]:
    live = [
        {"key": a.key, "label": a.label, "description": a.description, "available": True} for a in _ADAPTERS.values()
    ]
    planned = [{"key": k, "label": v, "description": "Coming soon", "available": False} for k, v in PLANNED.items()]
    return live + planned
