from app.domain.business_model import DiscoveryResult
from app.domain.validation import has_errors, normalize_model, validate_model
from app.llm.schema import strict_json_schema
from tests.factories import solar_model


def test_sample_model_is_valid_after_normalize():
    model = normalize_model(solar_model())
    issues = validate_model(model)
    assert not has_errors(issues), [i for i in issues if i.severity == "error"]


def test_normalize_syncs_stage_picklist_with_process():
    model = normalize_model(solar_model())
    stage = next(f for f in model.entity("deal").fields if f.key == "stage")
    assert [o.label for o in stage.options] == ["New Enquiry", "Site Visit Done", "Won", "Lost"]
    assert sum(o.is_default for o in stage.options) == 1


def test_validation_catches_broken_references():
    model = solar_model()
    model.entities[1].fields[1].reference_entity = "nope"
    model.automations[0].conditions[0].field = "missing_field"
    model.roles[1].reports_to = "ghost"
    codes = {i.code for i in validate_model(model) if i.severity == "error"}
    assert {"unknown_entity", "unknown_field", "unknown_role"} <= codes


def test_validation_detects_role_cycle_and_duplicate_keys():
    model = solar_model()
    model.roles[0].reports_to = "sales_rep"
    model.entities[4].key = "site_visit"
    codes = {i.code for i in validate_model(model)}
    assert "role_cycle" in codes and "duplicate_key" in codes


def _walk(node, path="$"):
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, path
            assert set(node["required"]) == set(node.get("properties", {})), path
        for bad in ("default", "minimum", "maxLength", "title"):
            assert bad not in node or path.endswith(".properties"), f"{path} has {bad}"
        if "$ref" in node:
            assert len(node) == 1, path
        for k, v in node.items():
            _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]")


def test_strict_schema_is_strict():
    schema = strict_json_schema(DiscoveryResult)
    _walk(schema)
    # property literally named "type" (Field_.type) must survive
    assert "type" in schema["$defs"]["Field_"]["properties"]
