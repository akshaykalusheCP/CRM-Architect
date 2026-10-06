import io
import zipfile

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.db.models import Project
from app.ingestion.salesforce_metadata import is_supported_path, parse_salesforce_bundle
from app.services.knowledge import retrieve_knowledge


def metadata_zip() -> bytes:
    files = {
        "force-app/main/default/objects/Account/Account.object-meta.xml":
            "<CustomObject><label>Account</label><fields><fullName>Status__c</fullName>"
            "<label>Status</label><type>Picklist</type></fields></CustomObject>",
        "force-app/main/default/flows/Account_Notify.flow-meta.xml":
            "<Flow><label>Account notification</label><processType>AutoLaunchedFlow</processType>"
            "<status>Active</status><start><object>Account</object>"
            "<triggerType>RecordAfterSave</triggerType></start></Flow>",
        "force-app/main/default/reports/Ops/Account_Report.report-meta.xml":
            "<Report><name>Account report</name><reportType>Account</reportType>"
            "<columns>ACCOUNT.NAME</columns></Report>",
        "retrieve-output/reports/Ops/Account_Report.report":
            "<Report><name>Account report</name><reportType>Account</reportType>"
            "<columns>ACCOUNT.NAME</columns></Report>",
        "force-app/main/default/namedCredentials/API.namedCredential-meta.xml":
            "<NamedCredential><label>API</label><namedCredentialType>SecuredEndpoint</namedCredentialType>"
            "<password>secret-must-not-be-stored</password></NamedCredential>",
        "force-app/main/default/classes/Service.cls-meta.xml":
            "<ApexClass><status>Active</status></ApexClass>",
        "force-app/main/default/classes/Service.cls": "public class Service {}",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def test_metadata_bundle_covers_categories_and_skips_apex_source():
    assert not is_supported_path("classes/Service.cls")
    assert not is_supported_path("triggers/Service.trigger")
    assert is_supported_path("classes/Service.cls-meta.xml")
    objects, categories = parse_salesforce_bundle(metadata_zip())
    assert len(objects) == 1
    assert objects[0]["fields"][0]["api_name"] == "Status__c"
    counts = {item["metadata_type"]: item["count"] for item in categories}
    assert counts == {"flows": 1, "reports": 1, "namedCredentials": 1, "classes": 1}
    assert "secret-must-not-be-stored" not in str(categories)


async def test_import_review_and_retrieval(auth_client):
    data = metadata_zip()
    response = await auth_client.post(
        "/api/knowledge/import", data={"industry": "Insurance"},
        files={"file": ("metadata.zip", data, "application/zip")},
    )
    assert response.status_code == 201, response.text
    entries = response.json()["entries"]
    assert len(entries) == 5
    assert {entry["kind"] for entry in entries} == {"salesforce_metadata", "salesforce_category"}
    assert all(entry["status"] == "pending" for entry in entries)

    project = await auth_client.post(
        "/api/projects", json={"name": "Insurance CRM", "client_name": "Client", "industry": "Insurance",
                                "description": "Accounts need notification and reports"},
    )
    assert project.status_code == 201
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(Project).where(Project.id == project.json()["id"]))
        assert row is not None
        assert not any(item.get("kind") == "salesforce_category" for item in await retrieve_knowledge(session, row))

    review = await auth_client.post(
        "/api/knowledge/review-bulk", json={"ids": [entry["id"] for entry in entries], "decision": "approve"},
    )
    assert review.status_code == 200, review.text
    assert review.json()["reviewed"] == 5
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(Project).where(Project.id == project.json()["id"]))
        knowledge = await retrieve_knowledge(session, row, "Account notification report API")
        assert any(item.get("kind") == "salesforce_category" for item in knowledge)

    repeat = await auth_client.post(
        "/api/knowledge/import", data={"industry": "Insurance"},
        files={"file": ("metadata.zip", data, "application/zip")},
    )
    assert repeat.status_code == 201, repeat.text
    assert repeat.json()["imported"] == 0


async def test_feedback_requires_review_and_matching_industry(auth_client):
    source = await auth_client.post(
        "/api/projects", json={"name": "Source", "client_name": "Client", "industry": "Insurance"},
    )
    same = await auth_client.post(
        "/api/projects", json={"name": "Same industry", "client_name": "Client", "industry": "Insurance"},
    )
    other = await auth_client.post(
        "/api/projects", json={"name": "Other industry", "client_name": "Client", "industry": "Logistics"},
    )
    feedback = await auth_client.post(
        f"/api/projects/{source.json()['id']}/knowledge",
        json={"title": "Claims routing", "content": "Route claims by severity and policy type."},
    )
    assert feedback.status_code == 201, feedback.text
    assert feedback.json()["status"] == "pending"
    async with get_sessionmaker()() as session:
        project = await session.scalar(select(Project).where(Project.id == same.json()["id"]))
        assert not any(item.get("kind") == "feedback" for item in await retrieve_knowledge(session, project))

    review = await auth_client.post(
        f"/api/projects/{source.json()['id']}/knowledge/{feedback.json()['id']}/review",
        json={"decision": "approve", "scope": "organization"},
    )
    assert review.status_code == 200, review.text
    assert review.json()["scope"] == "organization"
    async with get_sessionmaker()() as session:
        matching = await session.scalar(select(Project).where(Project.id == same.json()["id"]))
        unrelated = await session.scalar(select(Project).where(Project.id == other.json()["id"]))
        assert any(item.get("kind") == "feedback" for item in await retrieve_knowledge(session, matching, "claims"))
        assert not any(item.get("kind") == "feedback" for item in await retrieve_knowledge(session, unrelated, "claims"))
