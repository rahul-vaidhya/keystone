"""Citation page display (D3): parser page markers ("### Page N") give the cited page;
a whole-document section range (1..max) is hidden rather than shown as "Pages 1-36"."""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from app.middleware.context import TenantContext
from app.models.documents import Document
from app.models.ingestion import Chunk, Section
from app.services.ingestion import ingestion_service
from app.services.ingestion.search import derive_citation_pages
from main import app

# ---- pure ----


def test_marker_at_chunk_start_gives_that_page() -> None:
    pages = derive_citation_pages(
        "### Page 3\nAtoms bond.",
        preceding_marker_text=None,
        section_pages=(1, 36),
        document_max_page=36,
    )
    assert pages == (3, 3)


def test_markerless_chunk_uses_last_preceding_marker() -> None:
    pages = derive_citation_pages(
        "Atoms bond.",
        preceding_marker_text="### Page 6\nx\n### Page 7\ny",
        section_pages=(1, 36),
        document_max_page=36,
    )
    assert pages == (7, 7)


def test_chunk_spanning_a_page_break_gives_a_range() -> None:
    pages = derive_citation_pages(
        "end of page seven\n### Page 8\nstart of eight",
        preceding_marker_text="### Page 7\nstuff",
        section_pages=(1, 36),
        document_max_page=36,
    )
    assert pages == (7, 8)


def test_whole_document_range_is_hidden_but_real_section_range_kept() -> None:
    whole = derive_citation_pages(
        "text", preceding_marker_text=None, section_pages=(1, 36), document_max_page=36
    )
    assert whole == (None, None)
    real = derive_citation_pages(
        "text", preceding_marker_text=None, section_pages=(3, 4), document_max_page=36
    )
    assert real == (3, 4)
    single_page_doc = derive_citation_pages(
        "text", preceding_marker_text=None, section_pages=(1, 1), document_max_page=1
    )
    assert single_page_doc == (1, 1)


# ---- integration (real Postgres): get_chunks derives pages from earlier chunks ----


async def test_get_chunks_derives_page_from_preceding_marker_chunk(
    session_factory, tenant_engine
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/auth/signup",
            json={"email": "citepages@test.com", "password": "password123", "org_name": "CP"},
        )
        assert resp.status_code == 201
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        org_id = uuid.UUID((await client.get("/auth/me", headers=headers)).json()["org_id"])

    doc_id = uuid.uuid4()
    section_id = uuid.uuid4()
    texts = ["### Page 1\nIntro text here.", "### Page 2\nBonds form.", "More about bonds."]
    chunk_ids = [uuid.uuid4() for _ in texts]
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, title="Doc"))
        await session.flush()
        session.add(
            Section(
                id=section_id,
                org_id=org_id,
                document_id=doc_id,
                parent_section_id=None,
                ordinal=0,
                depth=0,
                path="1",
                heading="document.pdf",
                page_start=1,
                page_end=5,
                char_start=0,
                char_end=200,
            )
        )
        await session.flush()
        offset = 0
        for i, (cid, text) in enumerate(zip(chunk_ids, texts, strict=True)):
            session.add(
                Chunk(
                    id=cid,
                    org_id=org_id,
                    document_id=doc_id,
                    section_id=section_id,
                    ordinal=i,
                    content=text,
                    token_count=5,
                    char_start=offset,
                    char_end=offset + len(text),
                )
            )
            offset += len(text) + 1

    ctx = TenantContext(org_id=org_id)
    records = {r.chunk_id: r for r in await ingestion_service.get_chunks(ctx, chunk_ids)}
    assert (records[chunk_ids[0]].page_start, records[chunk_ids[0]].page_end) == (1, 1)
    assert (records[chunk_ids[2]].page_start, records[chunk_ids[2]].page_end) == (2, 2)

    # Another org sees nothing (the marker lookup is org-scoped like get_by_ids).
    assert await ingestion_service.get_chunks(TenantContext(org_id=uuid.uuid4()), chunk_ids) == []
