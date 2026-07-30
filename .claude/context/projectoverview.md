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
Originally scoped as small enough that "everyone in the org can see everything" was acceptable —
since superseded by Access Roles (tag-gated folder/document visibility, 2026-07-12) and
per-person notebook sharing (2026-07-27): the default is still open-within-org, but an org can
now genuinely restrict who sees what.

## Core user flows (MVP)
1. **Onboard:** sign up → create org → invite teammates.
2. **Populate:** upload PDF/DOCX/TXT/MD/HTML into folders; tag them; watch ingestion status
   move to `ready`.
3. **Curate:** create a notebook, add documents to it by reference (no copying).
4. **Ask:** open a notebook → ask a question → get a streamed, cited answer grounded only in
   that notebook's documents → click a citation to see the source span.

## In scope (MVP) — all shipped
- Org signup + auth; tenant isolation via always-on app-level `org_id` scoping **plus enforced
  Postgres RLS** (unconditional since F60, 2026-07-14 — not gated by any flag).
- Document repository: folder tree + tags; upload with checksum dedupe ("upload once").
- Staged ingestion pipeline with visible per-document status.
- Notebooks as references (a join table), not copies.
- Strictly source-grounded chat with inline citations and "I don't know".
- Admin **debug bundle** on answers (retrieved hits + scores + final prompt + raw output).

## Shipped beyond MVP (all flag-gated off by default unless noted; see architecture.md's
## "MVP / V2 / V3" table for the full build-now/postponed breakdown)
- **Access control**: tag-based Access Roles gating folder/document visibility AND folder
  mutation (2026-07-12/2026-07-27); per-person notebook sharing/privacy with no owner/admin
  bypass (2026-07-27); account deactivation + per-account login lockout + self-service password
  change (auth hardening, 2026-07-13).
- **Public embeddable website chatbot** (2026-07-23) — no longer "V3, postponed."
- **Retrieval quality**: a self-hosted reranker seam, hybrid (vector+BM25) search, a
  reranker-score confidence gate, contextual retrieval (P0/P1 rounds, 2026-07-28–29).
- **Cross-document synthesis**: a broad-query router + on-demand Notebook Overview (P1,
  2026-07-29) — answers "what's the gist" / "what should I be concerned about across N sources"
  questions the flat per-chunk retrieval path structurally cannot.
- **Feedback + eval**: persisted thumbs up/down (`message_feedback`), an admin-curatable golden
  question set sourced from real traces (`golden_questions`) — the Ragas metrics-grading harness
  itself is written but currently blocked by an upstream dependency incompatibility (unresolved
  as of 2026-07-30).

## Still out of scope — genuinely not built
- Connectors / sync (Google Drive, Notion, etc.) — manual upload only.
- Knowledge-graph retrieval (V3) — entity/relationship extraction, graph-then-vector.
- SSO/SAML/SCIM, audit logs, data residency (Enterprise).
- Per-user grants beyond Access Roles' tag-based model; email-based invite links (invites are a
  self-serve one-time token link, not email delivery — no email-provider seam exists).

## Success criteria (MVP "done")
- A new user can upload 20 of their own documents and get a correct, cited answer within minutes.
- Answers never cite content outside the selected notebook.
- No query can return another org's data (proven by an isolation test).
- Ingestion of a messy real-world PDF either succeeds with usable chunks or fails loudly with a
  recorded `failed_stage` — never silently produces garbage.

## Common mistakes to avoid (per this project's history)
- Letting bare folder *location* become the permission boundary — it's a folder's GRANTED TAG
  (Access Roles, inherits to the subtree) that gates access, never the raw path; an untagged/
  ungranted folder stays open to the whole org (see architecture.md).
- Treating "reference not duplicate" as hard; it's just a join table — do not over-build it.
- Generating per-section summaries during CORE ingestion (parse/structure/embed) — enrichment is
  a separate, later, flag-gated backfill stage (`ENRICHMENT_ENABLED`, shipped 2026-07-15), never
  folded into the core pipeline.
- Reaching for a dedicated vector DB or knowledge-graph retrieval before it's genuinely needed —
  pgvector + the flag-gated retrieval strategies already shipped (hierarchical, hybrid, reranked,
  broad-query map-reduce) cover real quality gaps; don't skip straight to a bigger hammer.
