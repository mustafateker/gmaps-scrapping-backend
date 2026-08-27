import csv
import io
import uuid
from contextlib import asynccontextmanager

import httpx
import redis
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.config import settings
from app.database import Base, engine, get_db
from app.gmaps_client import GmapsEngineClient, GmapsEngineError
from app.models import Job, JobStatus, Place
from app.schemas import JobCreateRequest, JobResponse, PaginatedPlaces, PlaceResponse
from app.security import require_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Google Maps Scraper Service", lifespan=lifespan)

if settings.allowed_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    status_report = {"database": "ok", "redis": "ok", "scraper_engine": "ok"}

    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        status_report["database"] = f"error: {exc}"

    try:
        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping()
    except Exception as exc:  # noqa: BLE001
        status_report["redis"] = f"error: {exc}"

    try:
        httpx.get(settings.gmaps_scraper_base_url, timeout=3)
    except Exception as exc:  # noqa: BLE001
        status_report["scraper_engine"] = f"error: {exc}"

    healthy = all(v == "ok" for v in status_report.values())
    return {"healthy": healthy, **status_report}


@app.post("/jobs", response_model=JobResponse, status_code=202, dependencies=[Depends(require_api_key)])
def create_job(payload: JobCreateRequest, db: Session = Depends(get_db)) -> Job:
    job = Job(params=payload.model_dump())
    db.add(job)
    db.commit()
    db.refresh(job)

    celery_app.send_task("app.tasks.run_scrape_job", args=[str(job.id)])

    return job


@app.get("/jobs", response_model=list[JobResponse], dependencies=[Depends(require_api_key)])
def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Job]:
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)

    return list(db.scalars(stmt))


@app.get("/jobs/{job_id}", response_model=JobResponse, dependencies=[Depends(require_api_key)])
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.delete("/jobs/{job_id}", status_code=204, dependencies=[Depends(require_api_key)])
def delete_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    if job.engine_job_id and job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        client = GmapsEngineClient(settings.gmaps_scraper_base_url)
        try:
            client.delete_job(job.engine_job_id)
        except GmapsEngineError:
            pass
        finally:
            client.close()

    db.delete(job)
    db.commit()


@app.get("/jobs/{job_id}/results", response_model=PaginatedPlaces, dependencies=[Depends(require_api_key)])
def get_job_results(
    job_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")

    total = db.scalar(select(func.count()).select_from(Place).where(Place.job_id == job_id))
    items = list(
        db.scalars(
            select(Place).where(Place.job_id == job_id).order_by(Place.id).limit(limit).offset(offset)
        )
    )

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@app.get("/jobs/{job_id}/results.csv", dependencies=[Depends(require_api_key)])
def get_job_results_csv(job_id: uuid.UUID, db: Session = Depends(get_db)) -> StreamingResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    rows = list(db.scalars(select(Place).where(Place.job_id == job_id).order_by(Place.id)))
    if not rows:
        raise HTTPException(status_code=404, detail="no results for this job yet")

    fieldnames = list(rows[0].raw.keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row.raw)

    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="job-{job_id}-results.csv"'}
    return StreamingResponse(buffer, media_type="text/csv", headers=headers)
