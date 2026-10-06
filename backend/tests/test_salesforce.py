import io
import xml.etree.ElementTree as ET
import zipfile

from app.adapters.salesforce.generator import SRC, SalesforceAdapter
from app.adapters.salesforce.naming import NameRegistry, api_base
from app.domain.validation import normalize_model
from tests.factories import solar_model

NS = {"sf": "http://soap.sforce.com/2006/04/metadata"}


def _generate():
    return SalesforceAdapter().generate(normalize_model(solar_model()), "SunPeak Solar")


def _xml(art, path):
    return ET.fromstring(art.files[path])


def test_naming_rules():
    assert api_base("system_size_kw") == "System_Size_Kw"
    assert api_base("2nd phone") == "X2nd_Phone"
    assert len(api_base("x" * 100)) == 40
    reg = NameRegistry()
    assert reg.claim("Status", "__c") == "Status__c"
    assert reg.claim("Status", "__c") == "Status_2__c"


def test_all_xml_is_well_formed():
    art = _generate()
    for path, content in art.files.items():
        if path.endswith(".xml"):
            ET.fromstring(content)
    buf = zipfile.ZipFile(io.BytesIO(art.to_zip("root")))
    assert "root/sfdx-project.json" in buf.namelist()


def test_standard_objects_are_reused_and_custom_objects_created():
    art = _generate()
    objects = {o["entity"]: o for o in art.details["objects"]}
    assert objects["customer"]["api_name"] == "Account"
    assert objects["contact_person"]["api_name"] == "Contact"
    assert objects["deal"]["api_name"] == "Opportunity"
    assert objects["site_visit"]["api_name"] == "Site_Visit__c"
    deal_fields = {f["key"]: f for f in objects["deal"]["fields"]}
    assert deal_fields["stage"]["api_name"] == "StageName" and deal_fields["stage"]["standard"]
    assert deal_fields["customer"]["api_name"] == "AccountId"
    assert objects["contact_person"]["fields"][1]["api_name"] == "AccountId"
    assert f"{SRC}/objects/Site_Visit__c/Site_Visit__c.object-meta.xml" in art.files
    assert f"{SRC}/objects/Account/fields/Gst_Number__c.field-meta.xml" in art.files
    assert not any(p.endswith("Account.object-meta.xml") for p in art.files)


def test_master_detail_and_sharing():
    art = _generate()
    obj = _xml(art, f"{SRC}/objects/Site_Visit__c/Site_Visit__c.object-meta.xml")
    assert obj.find("sf:sharingModel", NS).text == "ControlledByParent"
    assert obj.find("sf:nameField/sf:type", NS).text == "AutoNumber"
    md = _xml(art, f"{SRC}/objects/Site_Visit__c/fields/Deal__c.field-meta.xml")
    assert md.find("sf:type", NS).text == "MasterDetail"
    assert md.find("sf:referenceTo", NS).text == "Opportunity"
    assert md.find("sf:required", NS) is None
    inst = _xml(art, f"{SRC}/objects/Installation__c/Installation__c.object-meta.xml")
    assert inst.find("sf:sharingModel", NS).text == "Private"  # role hierarchy exists


def test_field_types():
    art = _generate()
    cb = _xml(art, f"{SRC}/objects/Site_Visit__c/fields/Roof_Ok__c.field-meta.xml")
    assert cb.find("sf:defaultValue", NS).text == "false" and cb.find("sf:required", NS) is None
    pk = _xml(art, f"{SRC}/objects/Site_Visit__c/fields/Status__c.field-meta.xml")
    values = pk.findall("sf:valueSet/sf:valueSetDefinition/sf:value", NS)
    assert [v.find("sf:fullName", NS).text for v in values] == ["Scheduled", "Completed"]
    num = _xml(art, f"{SRC}/objects/Opportunity/fields/System_Size_Kw__c.field-meta.xml")
    assert num.find("sf:scale", NS).text == "2"
    assert any("uniqueness" in w for w in art.warnings)  # unique phone dropped


def test_opportunity_stage_value_set():
    art = _generate()
    vs = _xml(art, f"{SRC}/standardValueSets/OpportunityStage.standardValueSet-meta.xml")
    values = vs.findall("sf:standardValue", NS)
    labels = [v.find("sf:label", NS).text for v in values]
    assert labels == ["New Enquiry", "Site Visit Done", "Won", "Lost"]
    won = values[2]
    assert won.find("sf:won", NS).text == "true" and won.find("sf:closed", NS).text == "true"


def test_permission_sets_and_roles():
    art = _generate()
    rep = _xml(art, f"{SRC}/roles/Sales_Rep.role-meta.xml")
    assert rep.find("sf:parentRole", NS).text == "Sales_Manager"
    ps = _xml(art, f"{SRC}/permissionsets/Sales_Rep_Access.permissionset-meta.xml")
    fields = [f.find("sf:field", NS).text for f in ps.findall("sf:fieldPermissions", NS)]
    # required and master-detail fields must not appear in field permissions
    assert "Site_Visit__c.Visit_Date__c" not in fields and "Site_Visit__c.Deal__c" not in fields
    assert "Site_Visit__c.Notes__c" in fields
    objs = {o.find("sf:object", NS).text for o in ps.findall("sf:objectPermissions", NS)}
    assert objs == {"Opportunity", "Site_Visit__c"}


def test_flows():
    art = _generate()
    flow = _xml(art, f"{SRC}/flows/Site_Visit_Visit_Follow_Up.flow-meta.xml")
    start = flow.find("sf:start", NS)
    assert start.find("sf:triggerType", NS).text == "RecordAfterSave"
    assert start.find("sf:recordTriggerType", NS).text == "Update"
    assert start.find("sf:filters/sf:operator", NS).text == "EqualTo"
    assert flow.find("sf:status", NS).text == "Draft"
    create = flow.find("sf:recordCreates", NS)
    assert create.find("sf:connector/sf:targetReference", NS).text.startswith("Send_Email")
    before = _xml(art, f"{SRC}/flows/Site_Visit_Default_Status.flow-meta.xml")
    assert before.find("sf:start/sf:triggerType", NS).text == "RecordBeforeSave"
    assert before.find("sf:start/sf:filters/sf:operator", NS).text == "IsNull"
    # unsupported pieces become manual steps instead of broken metadata
    assert any("Custom Notification" in s for s in art.manual_steps)
    assert any("scheduled-triggered flow" in s for s in art.manual_steps)
    assert not any("Install_Reminder" in p for p in art.files)


def test_manifest_and_migration_kit():
    art = _generate()
    pkg = _xml(art, "manifest/package.xml")
    types = {t.find("sf:name", NS).text for t in pkg.findall("sf:types", NS)}
    assert {"CustomObject", "CustomField", "Flow", "PermissionSet", "Role", "StandardValueSet"} <= types
    order = art.details["load_order"]
    assert order.index("Account") < order.index("Opportunity") < order.index("Site_Visit__c")
    assert f"{SRC}/objects/Account/fields/Legacy_Id__c.field-meta.xml" in art.files
    visit = next(v for k, v in art.files.items() if k.endswith("_Site_Visit__c.csv"))
    assert visit.startswith("Legacy_Id__c,") and "Deal__c" in visit  # parent has no legacy id -> SF Id
    opp = next(v for k, v in art.files.items() if k.endswith("_Opportunity.csv"))
    assert "Account.Legacy_Id__c" in opp  # parent has a legacy id -> upsert by relationship


def test_automation_status_preview():
    status = SalesforceAdapter().automation_status(normalize_model(solar_model()))
    assert status["default_status"] == {"status": "flow", "note": "Flow · draft"}
    assert status["visit_follow_up"]["status"] == "partial"  # task + email generated, notification manual
    assert status["install_reminder"] == {"status": "manual", "note": "Manual · scheduled path"}
