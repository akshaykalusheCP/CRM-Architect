import io
import zipfile

from app.services.jobs import wait_for_inline_jobs
from tests.test_profiler import MESSY_CSV

VISITS_CSV = "Visit Id,Client Name,Visit Date,Outcome\nV1,Ravi Kumar,2025-01-14,Good\nV2,Asha Patel,2025-01-18,Good\n"


async def test_auth_and_csrf(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401
    r = await client.post(
        "/api/auth/register",
        json={"org_name": "Org", "full_name": "A", "email": "a@example.com", "password": "long-enough-pw"},
    )
    assert r.status_code == 201
    # cookie session without the CSRF header is rejected on writes
    r = await client.post("/api/projects", json={"name": "P", "client_name": "C"})
    assert r.status_code == 403
    r = await client.post(
        "/api/projects", json={"name": "P", "client_name": "C"}, headers={"X-CSRF-Token": client.cookies["csrf_token"]}
    )
    assert r.status_code == 201
    r = await client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong-password"})
    assert r.status_code == 401


async def test_tenant_isolation(auth_client, client):
    r = await auth_client.post("/api/projects", json={"name": "Secret", "client_name": "C"})
    pid = r.json()["id"]
    client.cookies.clear()
    r = await client.post(
        "/api/auth/register",
        json={"org_name": "Other", "full_name": "B", "email": "b@example.com", "password": "long-enough-pw"},
    )
    assert r.status_code == 201
    assert (await client.get(f"/api/projects/{pid}")).status_code == 404
    assert (await client.get("/api/projects")).json() == []


async def test_full_flow(auth_client):
    c = auth_client
    r = await c.post(
        "/api/projects",
        json={
            "name": "SunPeak rollout",
            "client_name": "SunPeak Solar",
            "industry": "Solar",
            "description": "We sell rooftop solar. Leads come from Facebook, then a site visit, then install.",
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = await c.post(
        f"/api/projects/{pid}/sources",
        files=[
            ("files", ("leads.csv", MESSY_CSV.encode(), "text/csv")),
            ("files", ("visits.csv", VISITS_CSV.encode(), "text/csv")),
            ("files", ("sop.txt", b"Every site visit must be completed within 3 days of enquiry.", "text/plain")),
        ],
    )
    assert r.status_code == 201, r.text
    await wait_for_inline_jobs()
    sources = (await c.get(f"/api/projects/{pid}/sources")).json()
    assert [s["status"] for s in sources] == ["processed"] * 3

    r = await c.post(f"/api/projects/{pid}/sources", files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 422

    r = await c.post(f"/api/projects/{pid}/analyze")
    assert r.status_code == 202
    job_id = r.json()["id"]
    await wait_for_inline_jobs()
    job = (await c.get(f"/api/projects/{pid}/jobs/{job_id}")).json()
    assert job["status"] == "succeeded", job
    assert job["result"]["version"] == 1

    project = (await c.get(f"/api/projects/{pid}")).json()
    assert project["status"] == "needs_input" and project["current_version"] == 1

    model = (await c.get(f"/api/projects/{pid}/model")).json()
    entity_keys = {e["key"] for e in model["model"]["entities"]}
    assert {"lead", "visit"} <= entity_keys

    questions = (await c.get(f"/api/projects/{pid}/questions")).json()
    assert len(questions) == 2
    r = await c.patch(f"/api/projects/{pid}/questions/{questions[0]['id']}", json={"answer": "Enquiry > Visit > Won"})
    assert r.json()["status"] == "answered"
    r = await c.patch(f"/api/projects/{pid}/questions/{questions[1]['id']}", json={"status": "dismissed"})
    assert r.json()["status"] == "dismissed"

    r = await c.post(f"/api/projects/{pid}/analyze")
    await wait_for_inline_jobs()
    versions = (await c.get(f"/api/projects/{pid}/model/versions")).json()
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["source"] == "ai_refine"
    assert (await c.get(f"/api/projects/{pid}")).json()["status"] == "ready"

    # Manual edit with optimistic concurrency
    current = (await c.get(f"/api/projects/{pid}/model")).json()
    edited = current["model"]
    edited["entities"][0]["label"] = "Enquiry"
    r = await c.put(f"/api/projects/{pid}/model", json={"model": edited, "base_version": 1})
    assert r.status_code == 409
    r = await c.put(f"/api/projects/{pid}/model", json={"model": edited, "base_version": 2, "note": "Rename"})
    assert r.status_code == 200 and r.json()["version"] == 3
    r = await c.put(f"/api/projects/{pid}/model", json={"model": {"bad": 1}, "base_version": 3})
    assert r.status_code == 422

    # Exports
    r = await c.post(f"/api/projects/{pid}/exports", json={"kind": "salesforce"})
    assert r.status_code == 201, r.text
    export = r.json()
    assert export["report"]["platform"] == "salesforce"
    r = await c.get(f"/api/projects/{pid}/exports/{export['id']}/download")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith("sfdx-project.json") for n in names)
    assert any("Lead__c.object-meta.xml" in n for n in names)

    r = await c.post(f"/api/projects/{pid}/exports", json={"kind": "design_doc"})
    assert r.status_code == 201
    doc = await c.get(f"/api/projects/{pid}/exports/{r.json()['id']}/download")
    assert doc.text.startswith("# Solution Design: SunPeak rollout")
    assert "edited manually" in doc.text  # v3 was a manual edit

    r = await c.post(f"/api/projects/{pid}/exports", json={"kind": "zoho"})
    assert r.status_code == 422


async def test_analyze_without_input_fails_cleanly(auth_client):
    pid = (await auth_client.post("/api/projects", json={"name": "Empty", "client_name": "C"})).json()["id"]
    job = (await auth_client.post(f"/api/projects/{pid}/analyze")).json()
    await wait_for_inline_jobs()
    job = (await auth_client.get(f"/api/projects/{pid}/jobs/{job['id']}")).json()
    assert job["status"] == "failed" and "description" in job["error"]
    assert (await auth_client.get(f"/api/projects/{pid}")).json()["status"] == "error"
