"""Documents routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.documents import DocumentOut, FolderOut, TagOut

router = APIRouter(prefix="/documents", tags=["documents"])

router.post("/folders", response_model=FolderOut, status_code=201)(
    controllers.documents.create_folder
)
router.get("/folders", response_model=list[FolderOut])(controllers.documents.list_folders)
router.get("/folders/{folder_id}", response_model=FolderOut)(controllers.documents.get_folder)
router.delete("/folders/{folder_id}", status_code=204)(controllers.documents.delete_folder)
router.patch("/folders/{folder_id}", response_model=FolderOut)(controllers.documents.rename_folder)
router.post("/folders/{folder_id}/move", response_model=FolderOut)(
    controllers.documents.move_folder
)
router.patch("/folders/{folder_id}/restriction", response_model=FolderOut)(
    controllers.documents.set_folder_restriction
)
router.post("/tags", response_model=TagOut, status_code=201)(controllers.documents.create_tag)
router.get("/tags", response_model=list[TagOut])(controllers.documents.list_tags)
router.delete("/tags/{tag_id}", status_code=204)(controllers.documents.delete_tag)
router.post("/{document_id}/tags/{tag_id}", status_code=204)(controllers.documents.tag_document)
router.delete("/{document_id}/tags/{tag_id}", status_code=204)(controllers.documents.untag_document)
router.get("", response_model=list[DocumentOut])(controllers.documents.list_documents)
router.post("/upload")(controllers.documents.upload_document)
router.delete("/{document_id}", status_code=204)(controllers.documents.delete_document)
