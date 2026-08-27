"""Thin client for the REST API exposed by gosom/google-maps-scraper's
`-web` mode (see web/web.go and web/job.go in that repo). Endpoints and
JSON field names here are read straight from that source, not the
project's docs, since the docs don't spell out the request schema.
"""

import httpx

STATUS_PENDING = "pending"
STATUS_WORKING = "working"
STATUS_OK = "ok"
STATUS_FAILED = "failed"

TERMINAL_STATUSES = {STATUS_OK, STATUS_FAILED}


class GmapsEngineError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GmapsEngineClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def create_job(self, name: str, params: dict) -> str:
        body = {
            "name": name,
            "keywords": params["keywords"],
            "lang": params["language"],
            "zoom": params["zoom"],
            "lat": params.get("latitude") or "",
            "lon": params.get("longitude") or "",
            "fast_mode": params["fast_mode"],
            "radius": params["radius"],
            "depth": params["depth"],
            "email": params["email"],
            "extra_reviews": params["extra_reviews"],
            # the engine multiplies this by time.Second server-side, so it
            # expects plain seconds here, not nanoseconds.
            "max_time": params["max_time_seconds"],
            "proxies": params.get("proxies") or [],
        }

        resp = self._client.post("/api/v1/jobs", json=body)
        if resp.status_code != httpx.codes.CREATED:
            raise GmapsEngineError(self._error_message(resp), resp.status_code)

        return resp.json()["id"]

    def get_job(self, engine_job_id: str) -> dict:
        resp = self._client.get(f"/api/v1/jobs/{engine_job_id}")
        if resp.status_code != httpx.codes.OK:
            raise GmapsEngineError(self._error_message(resp), resp.status_code)

        return resp.json()

    def delete_job(self, engine_job_id: str) -> None:
        resp = self._client.delete(f"/api/v1/jobs/{engine_job_id}")
        if resp.status_code != httpx.codes.OK:
            raise GmapsEngineError(self._error_message(resp), resp.status_code)

    def download_csv(self, engine_job_id: str) -> bytes:
        resp = self._client.get(f"/api/v1/jobs/{engine_job_id}/download")
        if resp.status_code != httpx.codes.OK:
            raise GmapsEngineError(resp.text.strip() or "download failed", resp.status_code)

        return resp.content

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        try:
            return resp.json().get("message", resp.text)
        except ValueError:
            return resp.text.strip() or f"HTTP {resp.status_code}"
