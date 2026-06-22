# Veratas — Project Instructions

Private, source-grounded company knowledge base (NotebookLM-style), multi-tenant.
Stack: **FastAPI + async workers + Postgres/pgvector** backend, **Vite React** SPA.
Architecture: **modular monolith**. Full detail in `.claude/context/architecture.md`.

This file is the entry point Claude Code reads automatically every session. The
actual project instructions, workflows, and context live under `.claude/` and are
imported below.

## How this project is organized

- `.claude/orchestrator.md` — reading order, the build loop, the skills, and the hard rules. **Read this first.**
- `.claude/context/` — project knowledge: overview, architecture, code standards, library usage, UI tokens/rules/registry.
- `.claude/skills/` — the workflows (`/architect`, `/review`, `/recover`, `/imprint`, `/remember`).
- `.claude/buildplan.md` — sequenced feature roadmap with Definitions of Done.
- `.claude/memory.md` — durable session memory (decisions, gotchas).
- `.claude/progresstracker.md` — what is done / what is next.

## Always loaded

@.claude/orchestrator.md
@.claude/memory.md
@.claude/progresstracker.md
