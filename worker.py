"""Single worker per database. Interrupted jobs are marked failed at startup."""
import logging
import time

from sqlalchemy import update
from sqlalchemy.orm import Session

from app import Base, Job, engine, process_one

def mark_interrupted_jobs(database_engine):
    with Session(database_engine) as db:
        db.execute(update(Job).where(Job.status == "running").values(
            status="failed", error="Worker wurde unterbrochen. Bitte erneut ausführen."))
        db.commit()


if __name__ == "__main__":
    Base.metadata.create_all(engine)
    mark_interrupted_jobs(engine)
    while True:
        try:
            if process_one(engine):
                continue
        except Exception:
            logging.exception("Worker-Fehler; Neustart erforderlich.")
            raise
        time.sleep(1)
