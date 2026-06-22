# uirules.md — Visual & Behavioural Rules (LEAN)

> Small by design (thin SPA). The few rules that matter are the ones specific to a
> grounded-RAG product: status visibility, citations, and "I don't know" honesty.

## Layout
- Three zones in the app: **left** = repository/notebook navigation (folder tree, tags),
  **center** = chat or document view, **right** (optional) = sources / citations panel.
- Use the 4px spacing grid from `uitokens.md`. Consistent panel padding (`space-4`).

## Interactions
- All interactive elements have a hover transition. Disabled states are visibly muted.
- Long actions (upload, ingestion) never block the UI — show async status, let the user keep working.

## Ingestion status (must always be visible per document)
- Map status → token color: `uploaded/parsing/structuring/embedding` = `--warning` (with stage label),
  `ready` = `--success`, `failed` = `--danger` (show `failed_stage`, offer retry).
- Never show a document as usable until `ready`.

## Chat & grounding (the product's soul)
- Stream tokens as they arrive (SSE). Show a typing indicator while waiting.
- **Every claim that comes from sources shows a citation marker**; clicking it opens the exact
  source span (use `char_start/char_end`). Citations render in mono.
- When the assistant has no supporting sources, it says so plainly — design must make
  "I don't know" feel like a correct, trustworthy answer, not an error state.
- Show which notebook (and thus which documents) the chat is scoped to.

## Empty & error states
- Empty repository / empty notebook / no-results all get a helpful prompt, not a blank screen.
- Surface backend domain errors as readable messages, never raw stack traces.

## Don't
- Don't over-design. Don't invent components that aren't in `buildplan.md`. Don't hard-code colors.
