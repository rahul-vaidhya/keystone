"""Embed widget routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.embed import EmbedConfigOut, WidgetOut

router = APIRouter(prefix="/embed", tags=["embed"])

router.post("/widgets", response_model=WidgetOut, status_code=201)(controllers.embed.create_widget)
router.get("/widgets", response_model=list[WidgetOut])(controllers.embed.list_widgets)
router.patch("/widgets/{widget_id}", response_model=WidgetOut)(controllers.embed.update_widget)
router.delete("/widgets/{widget_id}", status_code=204)(controllers.embed.delete_widget)

router.get("/public/{org_id}/{public_id}/config", response_model=EmbedConfigOut)(
    controllers.embed.get_public_config
)
router.post("/public/{org_id}/{public_id}/stream")(controllers.embed.stream_public_chat)
