import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(app, "engine", engine)
    with TestClient(app.app) as client:
        yield client
    engine.dispose()


def test_entire_workflow(client):
    doc = client.post("/api/documents", files={"file": ("test.txt", "Kontakt: mail@example.org. Fällig: 2026-12-01. Summe: 10,00 EUR.")})
    assert doc.status_code == 201
    doc_id = doc.json()["id"]
    assert "Fällig" in client.get(f"/api/documents/{doc_id}").json()["text"]
    workflow = client.post("/api/workflows", json={"name": "Rechnung", "fields": ["emails", "dates", "amounts"]})
    assert workflow.status_code == 201
    result = client.post("/api/runs", json={"document_id": doc_id, "workflow_id": workflow.json()["id"]})
    assert result.status_code == 201
    assert result.json()["result"] == {"emails": ["mail@example.org"], "dates": ["2026-12-01"], "amounts": ["10,00 EUR"]}
    assert client.get("/api/runs").json() == [result.json()]


def test_invalid_input(client):
    assert client.post("/api/documents", files={"file": ("test.png", b"data")}).status_code == 415
    assert client.post("/api/documents", files={"file": ("test.pdf", b"not a PDF")}).status_code == 422
    assert client.post("/api/workflows", json={"name": "Test", "fields": ["unknown"]}).status_code == 422
    assert client.post("/api/runs", json={"document_id": 999, "workflow_id": 999}).status_code == 404


def test_update_delete_and_validated_ai_response(client, monkeypatch):
    doc = client.post("/api/documents", files={"file": ("test.txt", "Ein Testdokument.")}).json()
    flow = client.post("/api/workflows", json={"name": "Alt", "fields": ["words"]}).json()
    path = f"/api/workflows/{flow['id']}"
    assert client.put(path, json={"name": "Neu", "fields": ["summary"]}).json()["name"] == "Neu"

    def fake_ollama(url, **options):
        import httpx
        assert url.endswith("/api/chat")
        assert options["json"]["messages"][-1]["content"] == "Ein Testdokument."
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"message": {"content": '{"summary":"Kurzer Test."}'}})

    monkeypatch.setattr("extraction.httpx.post", fake_ollama)
    body = {"document_id": doc["id"], "workflow_id": flow["id"]}
    assert client.post("/api/runs", json=body).json()["result"] == {"summary": "Kurzer Test."}
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert client.get("/api/runs").json() == []
    assert client.post("/api/runs", json=body).status_code == 404


@pytest.mark.parametrize("candidate,status", [(2, "quoted"), (1, "unverified"), (99, "unverified"), (None, "missing")])
def test_amount_evidence(client, monkeypatch, candidate, status):
    from extraction import ApprovedAmount

    def choose(options, *_, **kwargs):
        assert kwargs["output_format"]["properties"]["candidate"]["enum"] == [None, 1, 2]
        assert "1: 4.800,00 EUR" in options and "2: 3.200,00 EUR" in options
        return ApprovedAmount(candidate=candidate)

    monkeypatch.setattr("extraction.chat", choose)
    doc = client.post("/api/documents", files={"file": ("test.txt", "Abgelehnt: 4.800,00 EUR.\n\nFreigegeben sind 3.200,00 EUR.")}).json()
    flow = client.post("/api/workflows", json={"name": "Betrag", "fields": ["approved_amount"]}).json()
    result = client.post("/api/runs", json={"document_id": doc["id"], "workflow_id": flow["id"]})
    assert result.status_code == 201
    assert result.json()["result"]["approved_amount"] == {
        "status": status, "value": "3.200,00 EUR" if status == "quoted" else None,
        "quote": "Freigegeben sind 3.200,00 EUR." if status == "quoted" else None,
    }


def test_review_and_export(client, monkeypatch):
    proposal = {"value": "4.800,00 EUR", "quote": "Angebot: 4.800,00 EUR.", "status": "quoted"}
    monkeypatch.setattr(app, "approved_amount", lambda _: proposal)
    doc = client.post("/api/documents", files={"file": ("test.txt", "Test")}).json()
    flow = client.post("/api/workflows", json={"name": "Betrag", "fields": ["approved_amount"]}).json()
    run = client.post("/api/runs", json={"document_id": doc["id"], "workflow_id": flow["id"]}).json()
    base = f"/api/runs/{run['id']}"
    assert client.get(base + "/amount-export/json").status_code == 409
    assert client.put(base + "/amount-review", json={"value": "=1"}).status_code == 422
    updated = client.put(base + "/amount-review", json={"value": "3.200,00 EUR"})
    assert updated.status_code == 200
    assert updated.json()["result"]["approved_amount"] == proposal
    assert client.get("/api/runs").json()[0]["result"]["amount_review"] == "3.200,00 EUR"
    assert client.get(base + "/amount-export/json").json()["verified_amount"] == "3.200,00 EUR"
    assert client.get(base + "/amount-export/csv").text.splitlines()[1] == f"{run['id']};{doc['id']};3.200,00 EUR"
