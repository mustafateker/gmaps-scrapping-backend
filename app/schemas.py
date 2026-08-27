import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class JobCreateRequest(BaseModel):
    keywords: list[str] = Field(..., min_length=1, description="Search terms, e.g. ['coffee shop new york']")
    language: str = Field("en", min_length=2, max_length=2, description="2-letter language code")
    depth: int = Field(10, ge=1, description="How many result pages to crawl per keyword")
    max_time_seconds: int = Field(600, ge=60, description="Hard time budget for the scrape job")
    zoom: int = Field(15, ge=0, le=21)
    latitude: str | None = None
    longitude: str | None = None
    fast_mode: bool = False
    radius: int = Field(10000, ge=0)
    email: bool = Field(False, description="Attempt to extract emails from business websites")
    extra_reviews: bool = Field(False, description="Fetch up to ~300 reviews per place instead of the summary only")
    proxies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_fast_mode_coordinates(self) -> "JobCreateRequest":
        if self.fast_mode and (not self.latitude or not self.longitude):
            raise ValueError("fast_mode requires both latitude and longitude")
        return self


class JobResponse(BaseModel):
    id: uuid.UUID
    status: str
    engine_job_id: str | None
    result_count: int
    error_message: str | None
    params: dict
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class PlaceResponse(BaseModel):
    id: int
    place_id: str | None
    cid: str | None
    title: str | None
    category: str | None
    address: str | None
    phone: str | None
    website: str | None
    latitude: float | None
    longitude: float | None
    review_count: int | None
    review_rating: float | None
    raw: dict

    model_config = {"from_attributes": True}


class PaginatedPlaces(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[PlaceResponse]
