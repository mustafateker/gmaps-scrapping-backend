# Google Maps Scraper Microservice

A Python microservice that wraps [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper)
(run as its own container, in `-web` mode) behind a stable REST API, with
Postgres-backed job/result persistence and a Celery worker that supervises
each scrape.

## Architecture

```
client -> FastAPI (api) -> Postgres (jobs/places)
                |
                v
           Celery task (worker) -> gosom/google-maps-scraper (-web mode, sidecar)
                |
                v
           parses CSV result -> stores rows in Postgres
```

The Go scraper itself is never modified or reimplemented — it's run
unmodified, in `-web` mode, exposing its own REST API. `docker-compose.yml`
here doesn't bundle that engine as a service; it expects one already
running and reachable at `GMAPS_SCRAPER_BASE_URL` (e.g. as a `-web`
process on the host, reached via `http://host.docker.internal:8080`, or
as your own container/service). This service's job is to give you a
stable, versioned API of your own, track jobs in a database you control,
and normalize results into query-able rows instead of leaving you to poll
someone else's CSV files.

The Celery worker's job (`app/tasks.py::run_scrape_job`) is a supervisor:
it submits a job to the scraper engine, polls its status, and on success
downloads and parses its CSV output into the `places` table.

## Requirements

- Docker and Docker Compose

## Setup

```bash
cp .env.example .env
# edit .env: set a real API_KEY and Postgres password
docker compose up -d --build
```

This starts four containers: `postgres`, `redis` (Celery broker/backend),
`gmaps-scraper` (the Go engine, internal-only), and this service's `api`
and `worker`.

The API is available at `http://localhost:8000`.

## API

All endpoints except `/health` require an `X-API-Key` header matching
`API_KEY` in `.env`. Set `API_KEY=""` to disable auth (local dev only).

### Create a scrape job

```bash
curl -X POST http://localhost:8000/jobs \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "keywords": ["coffee shop new york"],
    "language": "en",
    "depth": 5,
    "max_time_seconds": 600
  }'
```

Returns `202` with the job's id and status `queued`. The engine itself
enforces a few rules worth knowing up front: `language` must be exactly
2 characters, `depth` must be >= 1, and `fast_mode` requires both
`latitude` and `longitude` to be set.

### Check job status

```bash
curl http://localhost:8000/jobs/{job_id} -H "X-API-Key: $API_KEY"
```

`status` moves through `queued -> running -> completed` (or `failed`).
`result_count` and `error_message` are populated once the job leaves
`running`.

### List jobs

```bash
curl "http://localhost:8000/jobs?status=completed&limit=20" -H "X-API-Key: $API_KEY"
```

### Get results (JSON, paginated)

```bash
curl "http://localhost:8000/jobs/{job_id}/results?limit=100&offset=0" -H "X-API-Key: $API_KEY"
```

Each result has a handful of typed columns (`title`, `address`, `phone`,
`website`, `latitude`, `longitude`, `review_count`, `review_rating`, ...)
plus a `raw` field holding every column the scraper produced, in case you
need a field that isn't promoted to its own column.

### Get results (CSV)

```bash
curl "http://localhost:8000/jobs/{job_id}/results.csv" -H "X-API-Key: $API_KEY" -o results.csv
```

### Cancel / delete a job

```bash
curl -X DELETE http://localhost:8000/jobs/{job_id} -H "X-API-Key: $API_KEY"
```

Deletes the job and its results from Postgres. If the job is still
queued or running, this also best-effort deletes it from the scraper
engine.

## Configuration

See `.env.example` for all options. Notable ones:

- `POLL_INTERVAL_SECONDS` — how often the worker checks job status on the
  engine (default 10s).
- `POLL_TIMEOUT_BUFFER_SECONDS` — grace period added on top of a job's own
  `max_time_seconds` before the worker gives up and marks it `failed`
  (default 120s) — covers engine startup/network overhead, not just the
  scrape itself.
- `CLEANUP_ENGINE_JOB` — delete the job from the engine's own storage
  once results are pulled in, so its data folder doesn't grow forever.

## Scaling

Run more worker replicas to process jobs concurrently:

```bash
docker compose up -d --scale worker=3
```

Each `run_scrape_job` task blocks its worker slot for the job's whole
duration (it's a polling supervisor, not just a dispatch call), so
worker concurrency should be sized to how many scrapes you want running
against the engine at once, not to your API's request volume.

## Notes on the engine's REST API

`app/gmaps_client.py` talks to endpoints exposed by google-maps-scraper's
own `-web` mode (`web/web.go` / `web/job.go` in that repo):

- `POST /api/v1/jobs` — create a job
- `GET /api/v1/jobs/{id}` — job status (`pending` / `working` / `ok` / `failed`)
- `DELETE /api/v1/jobs/{id}` — delete a job
- `GET /api/v1/jobs/{id}/download` — CSV results

These aren't documented field-by-field in that project's README — the
request/response shapes here were read directly from its Go source, not
guessed, so if you upgrade the `gosom/google-maps-scraper` image, diff
its `web/` package before assuming this client still matches.
