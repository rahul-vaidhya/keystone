"""Business-logic services and infrastructure modules.

Import from specific submodules to avoid circular dependencies:
from app.services.auth import AuthService, auth_service
from app.services.documents import DocumentsService, documents_service
from app.services.ingestion import IngestionService, ingestion_service
from app.services.base import BaseRepository
from app.services.storage import ObjectStore, get_object_store
from app.services.queue import JobQueue, get_job_queue
from app.services.seams import EMBED_DIM, get_embedder, get_llm, get_parser
etc.
"""

from __future__ import annotations
