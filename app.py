"""Dokumente speichern und konfigurierbare Extraktionen ausführen."""
import io
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from extraction import summarize

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader
from sqlalchemy import ForeignKey, JSON, Text, create_engine, delete, select
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


class DocumentInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class DocumentDetail(DocumentInfo):
    text: str


FIELDS = ("emails", "dates", "amounts", "words", "summary")


class WorkflowInput(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")
    fields: list[str] = Field(min_length=1, max_length=5)


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
    result = {key: len(doc.text.split()) if key == "words" else
              list(dict.fromkeys(re.findall(PATTERNS[key], doc.text)))[:100]
              for key in workflow.fields if key != "summary"}
    if "summary" in workflow.fields:
        if len(doc.text) > 6000:
            raise HTTPException(422, "Für die KI-Zusammenfassung maximal 6.000 Zeichen verwenden.")
        try:
            result["summary"] = summarize(doc.text)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, "Ollama nicht erreichbar, Modell fehlt oder Antwort ungültig. Lokalen Dienst und Modell prüfen.") from None
    record = Run(**body.model_dump(), result=result)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
