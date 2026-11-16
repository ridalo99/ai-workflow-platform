"""Dokumente speichern und konfigurierbare Extraktionen ausführen."""
import csv
import io
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from extraction import approved_amount, summarize

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader
from sqlalchemy import ForeignKey, JSON, Text, create_engine, delete, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///workflow.db"))
LIMIT = 2_000_000


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    text: Mapped[str] = mapped_column(Text)


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    fields: Mapped[list[str]] = mapped_column(JSON)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"))
    result: Mapped[dict] = mapped_column(JSON)


class Job(Base):
    __tablename__ = "workflow_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"))
    fields: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="queued")
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    error: Mapped[str | None]


class JobInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    document_id: int
    workflow_id: int
    status: str
    run_id: int | None
    error: str | None


class DocumentInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class DocumentDetail(DocumentInfo):
    text: str


FIELDS = ("emails", "dates", "amounts", "words", "summary", "approved_amount")


class WorkflowInput(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")
    fields: list[str] = Field(min_length=1, max_length=6)


class WorkflowInfo(WorkflowInput):
    model_config = ConfigDict(from_attributes=True)
    id: int


class RunInput(BaseModel):
    document_id: int
    workflow_id: int


class RunInfo(RunInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    result: dict


@asynccontextmanager
async def lifespan(_app):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Workflow Studio", lifespan=lifespan)


def session():
    with Session(engine) as db:
        yield db


DB = Annotated[Session, Depends(session)]


@app.get("/api/documents", response_model=list[DocumentInfo])
def documents(db: DB):
    return list(db.scalars(select(Document).order_by(Document.id.desc())))


@app.get("/api/documents/{id}", response_model=DocumentDetail)
def document(id: int, db: DB):
    record = db.get(Document, id)
    if record is None:
        raise HTTPException(404, "Dokument nicht gefunden.")
    return record


@app.post("/api/documents", status_code=201, response_model=DocumentInfo)
def upload(file: UploadFile, db: DB):
    name = (file.filename or "").replace("\\", "/").split("/")[-1][:180]
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".txt", ".md"}:
        raise HTTPException(415, "Nur PDF, TXT und Markdown werden unterstützt.")
    data = file.file.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise HTTPException(413, "Datei zu groß (maximal 2 MB).")
    try:
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 30:
                raise ValueError()
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            text = data.decode("utf-8-sig")
    except Exception:
        raise HTTPException(422, "Nicht lesbar: UTF-8-Text oder unverschlüsseltes PDF bis 30 Seiten verwenden.")
    if not text.strip() or len(text) > LIMIT or "\x00" in text:
        raise HTTPException(422, "Kein nutzbarer Text gefunden. Bildscans benötigen OCR.")
    record = Document(name=name, text=text)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@app.delete("/api/documents/{id}", status_code=204)
def delete_document(id: int, db: DB):
    record = db.get(Document, id)
    if record is None:
        raise HTTPException(404, "Dokument nicht gefunden.")
    db.execute(delete(Job).where(Job.document_id == id))
    db.execute(delete(Run).where(Run.document_id == id))
    db.delete(record)
    db.commit()


@app.get("/api/workflows", response_model=list[WorkflowInfo])
def workflows(db: DB):
    return list(db.scalars(select(Workflow).order_by(Workflow.id.desc())))


@app.post("/api/workflows", status_code=201, response_model=WorkflowInfo)
def create_workflow(body: WorkflowInput, db: DB):
    if not set(body.fields) <= set(FIELDS):
        raise HTTPException(422, "Unbekannter Extraktionstyp.")
    record = Workflow(name=body.name.strip(), fields=list(dict.fromkeys(body.fields)))
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@app.put("/api/workflows/{id}", response_model=WorkflowInfo)
def update_workflow(id: int, body: WorkflowInput, db: DB):
    record = db.get(Workflow, id)
    if record is None:
        raise HTTPException(404, "Workflow nicht gefunden.")
    if not set(body.fields) <= set(FIELDS):
        raise HTTPException(422, "Unbekannter Extraktionstyp.")
    record.name = body.name.strip()
    record.fields = list(dict.fromkeys(body.fields))
    db.commit()
    db.refresh(record)
    return record


PATTERNS = {
    "emails": r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}",
    "dates": r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})\b",
    "amounts": r"\b\d+(?:\.\d{3})*(?:,\d{2})?\s*(?:€|EUR\b)",
}


@app.get("/api/runs", response_model=list[RunInfo])
def runs(db: DB):
    return list(db.scalars(select(Run).order_by(Run.id.desc())))


@app.post("/api/runs", status_code=201, response_model=RunInfo)
def execute(body: RunInput, db: DB):
    doc, workflow = db.get(Document, body.document_id), db.get(Workflow, body.workflow_id)
    if doc is None or workflow is None:
        raise HTTPException(404, "Dokument oder Workflow fehlt.")
    result = extract(doc.text, workflow.fields)
    record = Run(**body.model_dump(), result=result)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record

class AmountReview(BaseModel):
    value: str = Field(pattern=r"^(?:[0-9]{1,12}|[0-9]{1,3}(?:\.[0-9]{3}){1,3}),[0-9]{2} EUR$",
                       min_length=8, max_length=25)


def amount_run(id: int, db: Session) -> Run:
    run = db.get(Run, id)
    if run is None or "approved_amount" not in run.result:
        raise HTTPException(404, "Keine Betragsextraktion für diese Ausführung.")
    return run


@app.put("/api/runs/{id}/amount-review", response_model=RunInfo)
def review_amount(id: int, body: AmountReview, db: DB):
    run = amount_run(id, db)
    run.result = {**run.result, "amount_review": body.value}
    db.commit()
    db.refresh(run)
    return run


class ApprovalEvaluation(BaseModel):
    status: Literal["AUTO_APPROVED", "MANUAL_REVIEW"]
    thresholdCents: Annotated[int, Field(strict=True, ge=0)]


@app.get("/api/runs/{id}/approval", response_model=ApprovalEvaluation)
def evaluate_approval(id: int, db: DB):
    """Evaluate the current approval rule for a saved human-reviewed EUR amount."""
    run = amount_run(id, db)
    reviewed = run.result.get("amount_review")
    if reviewed is None:
        raise HTTPException(409, "Betrag zuerst prüfen und speichern.")

    amount_cents = int(reviewed.removesuffix(" EUR").replace(".", "").replace(",", ""))
    base_url = os.getenv("APPROVAL_SERVICE_URL", "http://127.0.0.1:8080").rstrip("/")
    try:
        response = httpx.post(
            f"{base_url}/api/approvals/evaluate",
            json={"amountCents": amount_cents},
            timeout=httpx.Timeout(3.0, connect=1.0),
            trust_env=False,
        )
        response.raise_for_status()
        return ApprovalEvaluation.model_validate_json(response.content)
    except httpx.RequestError:
        raise HTTPException(503, "Der Freigabedienst ist derzeit nicht erreichbar.") from None
    except (httpx.HTTPStatusError, ValueError):
        raise HTTPException(502, "Der Freigabedienst hat keine gültige Entscheidung geliefert.") from None


@app.get("/api/runs/{id}/amount-export/{format}")
def export_amount(id: int, format: Literal["json", "csv"], db: DB):
    run = amount_run(id, db)
    if "amount_review" not in run.result:
        raise HTTPException(409, "Betrag zuerst prüfen und speichern.")
    row = {"run_id": run.id, "document_id": run.document_id, "verified_amount": run.result["amount_review"]}
    headers = {"Content-Disposition": f'attachment; filename="betrag-{id}.{format}"'}
    if format == "json":
        return JSONResponse(row, headers=headers)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(row), delimiter=";")
    writer.writeheader()
    writer.writerow(row)
    return Response(buffer.getvalue(), media_type="text/csv", headers=headers)


def extract(text: str, fields: list[str]) -> dict:
    result = {key: len(text.split()) if key == "words" else
              list(dict.fromkeys(re.findall(PATTERNS[key], text)))[:100]
              for key in fields if key not in {"summary", "approved_amount"}}
    if {"summary", "approved_amount"}.intersection(fields):
        if len(text) > 6000:
            raise HTTPException(422, "Für KI-Workflows maximal 6.000 Zeichen verwenden.")
        try:
            if "summary" in fields:
                result["summary"] = summarize(text)
            if "approved_amount" in fields:
                result["approved_amount"] = approved_amount(text)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, "Ollama nicht erreichbar, Modell fehlt oder Antwort ungültig. Lokalen Dienst und Modell prüfen.") from None
    return result

@app.post("/api/jobs", status_code=202, response_model=JobInfo)
def enqueue(body: RunInput, db: DB):
    doc = db.get(Document, body.document_id)
    flow = db.get(Workflow, body.workflow_id)
    if doc is None or flow is None:
        raise HTTPException(404, "Dokument oder Workflow fehlt.")
    job = Job(**body.model_dump(), fields=flow.fields)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@app.get("/api/jobs", response_model=list[JobInfo])
def jobs(db: DB):
    return list(db.scalars(select(Job).order_by(Job.id.desc()).limit(100)))


def process_one(engine) -> bool:
    with Session(engine) as db:
        job_id = db.scalar(select(Job.id).where(Job.status == "queued").order_by(Job.id).limit(1))
        if job_id is None:
            return False
        claimed = db.execute(update(Job).where(Job.id == job_id, Job.status == "queued").values(status="running"))
        db.commit()
        if claimed.rowcount != 1:
            return False
        job = db.get(Job, job_id)
        if job is None:
            return True
        doc = db.get(Document, job.document_id)
        text, fields = doc.text if doc else "", job.fields
    try:
        result = extract(text, fields)
        error = None
    except HTTPException as exc:
        error = str(exc.detail)
    except Exception:
        error = "Verarbeitung fehlgeschlagen."
    with Session(engine) as db:
        job = db.get(Job, job_id, with_for_update=True)
        if job is None:
            return True
        if error:
            job.status, job.error = "failed", error
        else:
            run = Run(document_id=job.document_id, workflow_id=job.workflow_id, result=result)
            db.add(run)
            db.flush()
            job.status, job.run_id = "completed", run.id
        db.commit()
    return True
