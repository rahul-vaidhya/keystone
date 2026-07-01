# Orchestrator — How to Work in This Project

This file defines how the agent uses the context files, in what order, and which
workflow ("skill") to run when. It is imported by the root `CLAUDE.md`, so it is
loaded **first, every session**.

> Project: a private, source-grounded company knowledge base (NotebookLM-style).
> Stack: **FastAPI + async workers + Postgres/pgvector** backend, **Vite React** SPA.
> Architecture: **layer-first MVC monolith** (`app/{models,schemas,controllers,services,
> repositories,exceptions,tasks}/`, one file per domain inside each layer — locked
> 2026-07-01 refactor, was domain-first before), ports only for 3 seams (parser/embedder/llm),
> staged ingestion pipeline, flag-gated V2/V3 retrieval. See `.claude/context/architecture.md`.

---

## 1. Reading order (load only what the task needs — save tokens)

**Every session, always** (these are auto-imported by the root `CLAUDE.md`):
1. `.claude/memory.md`          — what happened last, open decisions, gotchas.
2. `.claude/progresstracker.md` — what is done / what is next.

**Before planning or implementing a backend feature:**
3. `.claude/context/projectoverview.md` — the "why" and scope boundaries.
4. `.claude/context/architecture.md`    — stack, module boundaries, schema, pipelines (HEAVYWEIGHT — read fully).
5. `.claude/context/codestandards.md`   — how code must be written.
6. `.claude/context/librarydocs.md`     — how THIS project uses FastAPI/SQLAlchemy/pgvector/arq and the 3 seams.

**Only when touching the SPA:**
7. `.claude/context/uitokens.md`, `.claude/context/uirules.md`, `.claude/context/uiregistry.md`
   (lean files — skip them entirely for backend work).

Do **not** load UI files for backend tasks. Do **not** load `librarydocs.md` for pure UI tasks.

---

## 2. The loop (run for EVERY feature)

```
Architect  →  Implement  →  Review  →  Remember
                 ↑                        |
                 └──────── Recover ───────┘  (only when stuck)
```

## 3. Skills = workflows (live in `.claude/skills/`, invoked as slash commands)

These are procedures, not code. They are language-agnostic.

- **architect** (`/architect`, before any non-trivial feature): read the context files above for the
  current `.claude/buildplan.md` feature, ask clarifying questions, then produce a written plan
  (files to touch, module boundaries respected, tests to add, Definition of Done).
  **Do not write code in this step.**
- **Implement**: build exactly the planned feature. Obey `.claude/context/codestandards.md` and the
  module-boundary rule (cross-module calls go through `service` only). Follow the package-layout
  convention (`architecture.md` "Package-layout convention"): start each layer file flat; promote to
  a subpackage only when it exceeds ~200 lines AND mixes 2+ genuinely independent responsibilities —
  never pad a module that doesn't need the split. Land tests with the code.
- **review** (`/review`, after implementing): verify — no SQL outside `repositories/<domain>.py`; no
  business logic in `controllers/<domain>.py`; every query scoped by `org_id`; external calls go through a seam; the feature's
  Definition of Done is met; **any layer file that has crossed the package-layout trigger (>200 lines
  AND 2+ independent responsibilities) has been promoted to a subpackage, and no module has been
  padded with files it doesn't need.** Report violations, **do not auto-fix** without confirmation.
- **recover** (`/recover`, when the agent spirals or tests fail mysteriously): stop adding code. Diagnose
  against `.claude/context/architecture.md`'s hard rules and the staged-pipeline status model. Propose the
  smallest targeted fix.
- **imprint** (`/imprint`, after building any reusable SPA component): add it to `.claude/context/uiregistry.md`.

## 4. Close every session

Run **remember** (`/remember save`): update `.claude/memory.md` (decisions, gotchas, next step) and tick
boxes in `.claude/progresstracker.md` with the commit ref. Never end a session without this.

---

## 5. Hard rules (the non-negotiables — full list in `.claude/context/architecture.md`)

1. A domain may call another domain **only through its `services/<domain>.py`**, never its
   repository or tables.
2. **No SQL outside `repositories/<domain>.py`.** No business logic in `controllers/<domain>.py`.
3. **Every query is scoped by `org_id`.** RLS is the backstop, not the excuse to skip it.
4. External services (LLM, embeddings, parser) are reached **only through a seam interface**.
5. Ingestion stages are **idempotent and resumable**; intermediate artifacts are persisted.
6. **Structural metadata is captured now; semantic enrichment (summaries/topics/graph) is
   designed-for now but populated later behind a flag.** Never break this split.
7. MVP retrieval is flat vector search. Hierarchical (V2) and graph (V3) are additive,
   behind flags, with **no schema rewrite**.
8. **Package-layout convention (locked F31 refactor, paths updated by the 2026-07-01 MVC
   refactor):** a layer file (`services/<domain>.py`/`repositories/<domain>.py`/etc.) is
   promoted to a subpackage only when it exceeds ~200 lines AND mixes 2+ independent
   responsibilities — never speculatively, never to pad a domain that doesn't need it
   (`retrieval` having no `models/retrieval.py`/`repositories/retrieval.py` is correct, not
   a gap). Full rule + the ORM metadata-registration caveat: `architecture.md` "Package-layout
   convention".
