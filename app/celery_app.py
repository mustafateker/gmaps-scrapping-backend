from celery import Celery

from app.config import settings

celery_app = Celery("gmaps_scraper", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Registers the @celery_app.task-decorated functions with this app.
import app.tasks  # noqa: E402,F401
