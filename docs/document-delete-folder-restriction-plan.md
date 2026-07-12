# Implementation Plan — Hard Document Delete + Folder-Based Access Restriction

> Produced via the `/architect` skill, 2026-07-12. Confirmed by the user — ready to build.
> This file is the durable record of the plan; `.claude/memory.md` will get a compressed
> entry once the feature lands (per the Remember skill), but this doc holds the full
> reasoning so a fresh session can pick it up without re-deriving it.

## What we are building

Two additive features, zero schema rewrite to existing tables:

1. **`DELETE /documents/{document_id}`** — permanently removes a document: the DB row
   (which cascades via existing FKs to sections/chunks/embeddings/document_tags/
   knowledge_base_documents — all already `ON DELETE CASCADE`), plus its object-store
   blob (new `ObjectStore.delete` method).
2. **Folder restriction** — a boolean flag on `folders` (`restricted`, default `false`).
   An org owner/admin can flip it on a folder; when on, that folder and everything in
   its subtree becomes invisible to `member`-role users in chat/retrieval search —
   owner/admin always see everything, unrestricted folders behave exactly as they do
   today (zero behavior change until someone opts in).

## Why now

Surfaced from a direct ask: "if i delete a file i want to be able to remove that file
for good" + "others can access it and ask questions ... based on the amount of data
they have access to." Both map onto gaps already named in `.claude/memory.md` as
deferred: there was previously **no document delete endpoint at all**, and
`resolve_allowed_documents()` was an MVP stub literally returning every document in the
org ("V2 groups/grants" — named but never built). This plan pulls that forward, scoped
down to what was actually asked for (folder-based, role-granularity, retrieval-only).

## Language agreed on

- **"Restricted folder"**: a folder with `restricted = true`, or any ancestor with
  `restricted = true` (inheritance is downward through the subtree; a child folder's
  own `restricted` flag never turns OFF a parent's restriction — it can only add more).
- **"Allowed documents"**: what `resolve_allowed_documents()` returns — for owner/admin,
  all org docs (unchanged); for member, all org docs *except* those sitting in a
  restricted subtree. Root-level documents (`folder_id IS NULL`) are always allowed —
  there's no folder object to restrict them.
- **"For good"**: DB row + blob gone. Old chat citations keep their frozen `content`
  snapshot (already stored at ask-time per F41) but a future "view live source" link
  would 404 — accepted trade-off.

## Decisions made

- **Grant granularity → role-based, not per-user.** No new join table. Just
  `folders.restricted: bool`. Considered and rejected a per-user `folder_grants(org_id,
  folder_id, user_id)` table — the user explicitly chose role-based (member vs
  owner/admin) over per-individual-user grants.
- **Default → open until restricted.** Existing orgs see no behavior change until an
  admin explicitly restricts a folder. (Rejected: closed-by-default, which would have
  been a breaking change for every existing org's members.)
- **Owner/admin always bypass restriction.** Reuses the existing `ADMIN_ROLES`/
  `require_admin` dependency (`app/utils/constants.py`, `app/middleware/deps.py`)
  already used by invite/role-change endpoints.
- **Scope boundary — restriction only gates chat/retrieval (`resolve_allowed_documents`
  in `app/services/retrieval.py`), NOT the folder/document browsing endpoints**
  (`GET /documents`, `GET /documents/folders`). The ask was specifically "ask questions
  based on data they have access to." Gating the browsing UI too is a reasonable next
  step but touches `list_documents`/`get_document`, which are also called internally by
  ingestion/knowledge services with non-interactive contexts (workers have `role=None`)
  — folding restriction into those shared accessors risks silently breaking the
  pipeline. **Known named gap**: a restricted folder's documents are still visible via
  the plain browsing API to members; only chat/retrieval is gated. Flag if browsing
  should be gated too — that's a separate, slightly riskier follow-up, not built here.
- **`ObjectStore` gains a `delete(key)` method**, amending the previously-locked
  "put+get only" decision from `.claude/memory.md` — this feature is exactly the
  trigger that decision named for a future change ("Delete rides with the future
  orphan-sweep feature" — this is that feature). Deleting a missing key is a no-op
  (idempotent), not an error.
- **Delete order**: DB row deleted inside a transaction first (cascades handle
  children); blob deleted after commit. If blob delete fails, we get an orphaned blob,
  not a broken document — safer than the reverse order (which could leave a document
  row pointing at a missing blob if the DB delete then failed).
- **No role gating on document delete itself** — matches the existing (ungated)
  folder-delete convention (any org member can delete any org document today via
  folder cascade/reflow too, so this isn't new permissiveness).

## Assumptions

- In-flight ingestion jobs for a deleted document will hit `DocumentNotFound` and
  fail/retry-exhaust — acceptable, same class as the already-named "lost enqueue" gap
  in memory.md.
- Migration number `0011` is claimed by `folders.restricted`; F42's future
  `message_traces` migration becomes `0012`.

## How to build it

1. **Migration 0011**: `ALTER TABLE folders ADD COLUMN restricted boolean NOT NULL
   DEFAULT false`.
2. **`app/models/documents.py`**: add `restricted: Mapped[bool]` to `Folder`; add
   `restricted: bool` to `FolderOut`; new `FolderRestrictionUpdate(BaseModel)` schema.
3. **`app/services/storage.py`**: add `delete(key)` to the `ObjectStore` Protocol,
   `R2ObjectStore` (boto3 `delete_object`), `LocalDiskObjectStore` (remove file, swallow
   not-found).
4. **`app/services/documents/folders.py`**: add `set_restricted(ctx, folder_id,
   restricted)` (admin-only, enforced in controller) — plain field update, no cascade
   needed since inheritance is computed at read-time, not materialized.
5. **`app/services/documents/documents.py`**: add `DocumentRepository.delete(document)`
   + service `delete_document(ctx, document_id, *, object_store)` per the order above.
6. **`app/services/documents/__init__.py`**: expose `set_restricted` and
   `delete_document` on `documents_service`.
7. **`app/services/retrieval.py`**: rewrite `resolve_allowed_documents` — bypass for
   `ADMIN_ROLES`; for others, one top-down pass over `documents_service.list_folders(ctx)`
   (already ordered parent-before-child by path) computing inherited-restricted per
   folder, then filter `list_documents` by it.
8. **`app/controllers/documents.py` + `app/routes/documents.py`**: wire
   `DELETE /{document_id}` and `PATCH /folders/{folder_id}/restriction` (admin-gated).
9. **Tests**: hard-delete cascade + blob removal + cross-org 404 (`test_documents.py`);
   restriction inheritance, owner/admin bypass, live toggle with no separate resync
   step, admin-only gate on the toggle endpoint (`test_retrieval.py` + `test_documents.py`).
10. Update `architecture.md`'s "folder is NOT a permission boundary" line to reflect the
    new opt-in restriction capability.

## Definition of Done

- `DELETE /documents/{id}` removes the document row, sections, chunks, embeddings,
  document_tags, and knowledge_base_documents rows, and the object-store blob. Deleting
  a non-existent or cross-org document 404s.
- A `member`-role user cannot retrieve/chat-answer from documents inside a
  `restricted` folder (or its subtree); an `owner`/`admin` can regardless.
- Toggling `restricted` takes effect on the very next request — no separate
  re-index/resync step, since retrieval already queries live per-request.
- Unrestricted folders (the default) behave identically to today for every role.
- Full suite green (backend pytest + ruff, frontend vitest + tsc + build), independent
  review pass clean against the 8 hard rules in `orchestrator.md`.
