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
