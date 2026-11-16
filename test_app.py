import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
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


def test_background_job_persists_and_uses_submitted_fields(client):
    doc = client.post("/api/documents", files={"file": ("demo.txt", "Drei kleine Wörter.")}).json()
    flow = client.post("/api/workflows", json={"name": "Demo", "fields": ["words"]}).json()
    response = client.post("/api/jobs", json={"document_id": doc["id"], "workflow_id": flow["id"]})
    assert response.status_code == 202
    assert client.get("/api/jobs").json()[0]["status"] == "queued"
    assert client.get("/api/runs").json() == []
    client.put(f"/api/workflows/{flow['id']}", json={"name": "Neu", "fields": ["dates"]})
    assert app.process_one(app.engine)
    job = client.get("/api/jobs").json()[0]
    assert job["status"] == "completed"
    assert client.get("/api/runs").json()[0]["result"] == {"words": 3}
    assert job["run_id"] == client.get("/api/runs").json()[0]["id"]


def test_background_failure_is_visible(client, monkeypatch):
    def unavailable(url, **options):
        raise httpx.ConnectError("offline", request=httpx.Request("POST", url))

    monkeypatch.setattr("extraction.httpx.post", unavailable)
    doc = client.post("/api/documents", files={"file": ("demo.txt", "Demo")}).json()
    flow = client.post("/api/workflows", json={"name": "KI", "fields": ["summary"]}).json()
    client.post("/api/jobs", json={"document_id": doc["id"], "workflow_id": flow["id"]})
    assert app.process_one(app.engine)
    job = client.get("/api/jobs").json()[0]
    assert job["status"] == "failed" and "Ollama" in job["error"]
    assert client.get("/api/runs").json() == []


@pytest.mark.parametrize("content,status", [
    (b"", 422),
    (b"\xff", 422),
    (b"Text\x00", 422),
    (b"x" * (app.LIMIT + 1), 413),
])
def test_rejected_upload_is_not_stored(client, content, status):
    response = client.post("/api/documents", files={"file": ("test.txt", content)})
    assert response.status_code == status
    assert client.get("/api/documents").json() == []


@pytest.mark.parametrize("content", ["not JSON", '{"summary": "   "}', '{"summary": 42}'])
def test_invalid_ollama_output_does_not_create_run(client, monkeypatch, content):
    def invalid_response(url, **options):
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"message": {"content": content}})

    monkeypatch.setattr("extraction.httpx.post", invalid_response)
    doc = client.post("/api/documents", files={"file": ("demo.txt", "Demo")}).json()
    flow = client.post("/api/workflows", json={"name": "KI", "fields": ["summary"]}).json()
    body = {"document_id": doc["id"], "workflow_id": flow["id"]}

    assert client.post("/api/runs", json=body).status_code == 502
    assert client.post("/api/jobs", json=body).status_code == 202
    assert app.process_one(app.engine)
    job = client.get("/api/jobs").json()[0]
    assert job["status"] == "failed"
    assert "Antwort ungültig" in job["error"]
    assert client.get("/api/runs").json() == []


def test_worker_restart_recovers_queue_without_duplicate_runs(client):
    from worker import mark_interrupted_jobs

    doc = client.post("/api/documents", files={"file": ("demo.txt", "Ein Test")}).json()
    flow = client.post("/api/workflows", json={"name": "Test", "fields": ["words"]}).json()
    body = {"document_id": doc["id"], "workflow_id": flow["id"]}
    completed = client.post("/api/jobs", json=body).json()["id"]
    assert app.process_one(app.engine)
    interrupted = client.post("/api/jobs", json=body).json()["id"]
    waiting = client.post("/api/jobs", json=body).json()["id"]

    with Session(app.engine) as db:
        db.get(app.Job, interrupted).status = "running"
        db.commit()

    mark_interrupted_jobs(app.engine)
    mark_interrupted_jobs(app.engine)
    jobs = {job["id"]: job for job in client.get("/api/jobs").json()}
    assert jobs[completed]["status"] == "completed"
    assert jobs[interrupted]["status"] == "failed"
    assert "Worker wurde unterbrochen" in jobs[interrupted]["error"]
    assert jobs[waiting]["status"] == "queued"

    assert app.process_one(app.engine)
    assert not app.process_one(app.engine)
    assert len(client.get("/api/runs").json()) == 2


@pytest.fixture
def reviewable_run(client, monkeypatch):
    monkeypatch.setattr(app, "approved_amount", lambda _: {"value": "999,00 EUR"})
    doc = client.post("/api/documents", files={"file": ("demo.txt", "Test")}).json()
    flow = client.post("/api/workflows", json={"name": "Betrag", "fields": ["approved_amount"]}).json()
    run = client.post("/api/runs", json={"document_id": doc["id"], "workflow_id": flow["id"]}).json()
    return f"/api/runs/{run['id']}"


def test_approval_uses_only_saved_review(client, reviewable_run, monkeypatch):
    monkeypatch.setenv("APPROVAL_SERVICE_URL", "http://approval:8080/")

    def evaluate(url, **options):
        assert url == "http://approval:8080/api/approvals/evaluate"
        assert options["json"] == {"amountCents": 320050}
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"status": "MANUAL_REVIEW", "thresholdCents": 300000})

    monkeypatch.setattr(app.httpx, "post", evaluate)
    assert client.get("/api/runs/999/approval").status_code == 404
    assert client.get(reviewable_run + "/approval").status_code == 409
    assert client.put(reviewable_run + "/amount-review", json={"value": "3.200,50 EUR"}).status_code == 200
    response = client.get(reviewable_run + "/approval")
    assert response.status_code == 200
    assert response.json() == {"status": "MANUAL_REVIEW", "thresholdCents": 300000}


@pytest.mark.parametrize("failure,expected", [("offline", 503), ("http", 502), ("invalid", 502)])
def test_approval_service_failure_preserves_review_and_export(client, reviewable_run, monkeypatch, failure, expected):
    def evaluate(url, **options):
        request = httpx.Request("POST", url)
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        if failure == "http":
            return httpx.Response(500, request=request)
        return httpx.Response(200, request=request, json={"status": "UNKNOWN", "thresholdCents": 0})

    monkeypatch.setattr(app.httpx, "post", evaluate)
    assert client.put(reviewable_run + "/amount-review", json={"value": "3.200,00 EUR"}).status_code == 200
    assert client.get(reviewable_run + "/approval").status_code == expected
    assert client.get(reviewable_run + "/amount-export/json").json()["verified_amount"] == "3.200,00 EUR"
