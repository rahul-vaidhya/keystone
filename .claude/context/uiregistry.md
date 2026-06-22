# uiregistry.md — Component Catalogue (LEAN)

> Populated by the **Imprint** skill after building any reusable component, to prevent
> duplicates and drift. Seeded below with the components Phase 5 will introduce; fill in
> real prop/class details as you build them.

## Conventions
- Feature-folder components live with their feature; shared ones in `src/components/`.
- Each entry: name · purpose · key props · token usage. Add a one-line visual description.

## Components (to be built in Phase 5 — update as implemented)
- **StatusBadge** — shows document ingestion state. Props: `status`, `failedStage?`.
  Colors from tokens (`--warning/success/danger`). _[not built]_
- **UploadDropzone** — drag/drop or pick files; emits upload events; shows per-file progress. _[not built]_
- **FolderTree** — recursive nav of the folder tree; selectable; tag filter. _[not built]_
- **NotebookSelector** — pick which documents are in scope for a chat. _[not built]_
- **ChatMessage** — renders a streamed answer with inline **citation markers** (mono); click →
  opens source span. Props: `role`, `content`, `citations[]`. _[not built]_
- **SourcePanel** — shows the cited chunk's text with `char_start/char_end` highlighted. _[not built]_
- **DebugBundle** (admin only) — collapsible: retrieved hits + scores, final prompt, raw output. _[not built]_

## Rule
Before creating a new component, check this list. Reuse or extend an existing one rather than
duplicating. Run **Imprint** to record anything new here.
