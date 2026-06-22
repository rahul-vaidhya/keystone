# projectoverview.md — The Big Picture

## Concept
A private, **source-grounded company knowledge base** in the spirit of NotebookLM, but
multi-tenant and built for organizations. Users upload company documents once into a central
repository, organize them with folders and tags, group a subset into a "notebook" (knowledge
base), and chat with that notebook. Every answer is grounded strictly in the selected sources
and cites them; the assistant says "I don't know" rather than inventing.

## Goal
Let a team get trustworthy, cited answers from their own documents in seconds, instead of
hunting through a wiki or shared drive. **Answer quality + grounding discipline is the product**,
not a feature.

## Target audience (MVP)
A single team or department inside a company (e.g. Support, or HR, or one product group).
Small enough that "everyone in the org can see everything in the org" is acceptable for now.

## Core user flows (MVP)
1. **Onboard:** sign up → create org → invite teammates.
2. **Populate:** upload PDF/DOCX/TXT/MD/HTML into folders; tag them; watch ingestion status
   move to `ready`.
3. **Curate:** create a notebook, add documents to it by reference (no copying).
4. **Ask:** open a notebook → ask a question → get a streamed, cited answer grounded only in
   that notebook's documents → click a citation to see the source span.

## In scope (MVP)
- Org signup + auth; tenant isolation via **always-on app-level `org_id` scoping**. (Enforced
  Postgres RLS — the restricted-role backstop — is designed now but **deliberately deferred to
  Phase 6 (Security Hardening)**, gated by `RLS_ENABLED`, switched on before real customer data.)
- Document repository: folder tree + tags; upload with checksum dedupe ("upload once").
- Staged ingestion pipeline with visible per-document status.
- Notebooks as references (a join table), not copies.
- Strictly source-grounded chat with inline citations and "I don't know".
- Admin **debug bundle** on answers (retrieved hits + scores + final prompt + raw output).

## Out of scope (MVP) — designed-for, postponed
- Public embeddable website chatbot (V3).
- Connectors / sync (Google Drive, Notion, etc.) — manual upload only for now.
- Advanced ACL / groups / per-document permissions (V2). MVP = all org docs visible in-org.
- Hierarchical / indexed retrieval (V2) and knowledge-graph retrieval (V3).
- Reranking, hybrid (vector+BM25) search (V2).
- SSO/SAML/SCIM, audit logs, data residency (Enterprise).
- RAG eval frameworks (manual golden-questions checklist instead).

## Success criteria (MVP "done")
- A new user can upload 20 of their own documents and get a correct, cited answer within minutes.
- Answers never cite content outside the selected notebook.
- No query can return another org's data (proven by an isolation test).
- Ingestion of a messy real-world PDF either succeeds with usable chunks or fails loudly with a
  recorded `failed_stage` — never silently produces garbage.

## Common mistakes to avoid (per this project's history)
- Letting folder *location* become the permission boundary (it must not — see architecture.md).
- Treating "reference not duplicate" as hard; it's just a join table — do not over-build it.
- Generating per-section summaries at ingest time in the MVP (that's the postponed V2 enrichment).
- Reaching for a dedicated vector DB, knowledge graph, or agentic retrieval before V2/V3.
