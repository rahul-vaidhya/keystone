"""Structured logging via structlog.

Call ``configure_logging()`` once at process startup (FastAPI and the arq worker).
Phase 0 wires the renderer and level; ``request_id`` propagation from the HTTP edge into
arq job payloads is added with the request layer in Phase 1.
"""

from __future__ import annotations

import logging

import structlog

from app.config.settings import settings


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=settings.LOG_LEVEL)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(settings.LOG_LEVEL, logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
