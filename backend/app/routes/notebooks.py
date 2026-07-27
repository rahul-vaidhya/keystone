"""Notebooks routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.documents import DocumentOut
from app.models.knowledge import NotebookOut, NotebookShareOut

router = APIRouter(prefix="/notebooks", tags=["notebooks"])

router.post("", response_model=NotebookOut, status_code=201)(controllers.notebooks.create_notebook)
router.get("", response_model=list[NotebookOut])(controllers.notebooks.list_notebooks)
router.get("/{notebook_id}", response_model=NotebookOut)(controllers.notebooks.get_notebook)
router.patch("/{notebook_id}", response_model=NotebookOut)(controllers.notebooks.update_notebook)
router.delete("/{notebook_id}", status_code=204)(controllers.notebooks.delete_notebook)
router.post("/{notebook_id}/documents/{document_id}", status_code=204)(
    controllers.notebooks.attach_document
)
router.delete("/{notebook_id}/documents/{document_id}", status_code=204)(
    controllers.notebooks.detach_document
)
router.get("/{notebook_id}/documents", response_model=list[DocumentOut])(
    controllers.notebooks.list_notebook_documents
)
router.get("/{notebook_id}/shares", response_model=list[NotebookShareOut])(
    controllers.notebooks.list_shares
)
router.post("/{notebook_id}/shares", status_code=204)(controllers.notebooks.share_notebook)
router.delete("/{notebook_id}/shares/{user_id}", status_code=204)(
    controllers.notebooks.unshare_notebook
)
