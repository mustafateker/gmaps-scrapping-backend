import csv
import io
import logging
import time
import uuid
from datetime import datetime, timezone

from celery import shared_task

from app.config import settings
from app.database import SessionLocal
from app.gmaps_client import (
    STATUS_FAILED,
    STATUS_OK,
    GmapsEngineClient,
    GmapsEngineError,
)
from app.models import Job, JobStatus, Place

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _first(row: dict, *keys: str) -> str | None:
    lower_map = {k.lower(): v for k, v in row.items()}
    for key in keys:
        value = lower_map.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _as_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _as_int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None


def _rows_to_places(job_id, rows: list[dict]) -> list[Place]:
    places = []
    for row in rows:
        places.append(
            Place(
                job_id=job_id,
                place_id=_first(row, "place_id"),
                cid=_first(row, "cid"),
                title=_first(row, "title", "name"),
                category=_first(row, "category"),
                address=_first(row, "address", "complete_address"),
                phone=_first(row, "phone"),
                website=_first(row, "website"),
                latitude=_as_float(_first(row, "latitude", "lat")),
                longitude=_as_float(_first(row, "longitude", "lon", "lng")),
                review_count=_as_int(_first(row, "review_count", "reviews_count")),
                review_rating=_as_float(_first(row, "review_rating", "rating")),
                raw=row,
            )
        )
    return places


@shared_task(name="app.tasks.run_scrape_job")
def run_scrape_job(job_id: str) -> None:
    db = SessionLocal()
    client = GmapsEngineClient(settings.gmaps_scraper_base_url)

    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None:
            logger.error("job %s not found", job_id)
            return

        job.status = JobStatus.RUNNING
        job.started_at = _utcnow()
        db.commit()

        try:
            engine_job_id = client.create_job(name=f"job-{job.id}", params=job.params)
            job.engine_job_id = engine_job_id
            db.commit()

            deadline = time.monotonic() + job.params["max_time_seconds"] + settings.poll_timeout_buffer_seconds

            while True:
                engine_job = client.get_job(engine_job_id)
                engine_status = engine_job["Status"]

                if engine_status == STATUS_OK:
                    break
                if engine_status == STATUS_FAILED:
                    raise GmapsEngineError("scrape engine reported job status 'failed'")
                if time.monotonic() > deadline:
                    raise GmapsEngineError(
                        f"timed out waiting for engine job {engine_job_id} "
                        f"(last status: {engine_status})"
                    )

                time.sleep(settings.poll_interval_seconds)

            csv_bytes = client.download_csv(engine_job_id)
            reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")))
            rows = list(reader)

            db.add_all(_rows_to_places(job.id, rows))
            job.result_count = len(rows)
            job.status = JobStatus.COMPLETED
            job.finished_at = _utcnow()
            db.commit()

            if settings.cleanup_engine_job:
                try:
                    client.delete_job(engine_job_id)
                except GmapsEngineError as exc:
                    logger.warning("failed to clean up engine job %s: %s", engine_job_id, exc)

        except GmapsEngineError as exc:
            db.rollback()
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            job.finished_at = _utcnow()
            db.commit()
            raise
    finally:
        client.close()
        db.close()
