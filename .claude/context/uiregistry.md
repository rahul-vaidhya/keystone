# uiregistry.md — Component Catalogue (LEAN)

> Populated by the **Imprint** skill after building any reusable component, to prevent
> duplicates and drift. Seeded below with the components Phase 5 will introduce; fill in
> real prop/class details as you build them.

## Conventions
- Feature-folder components live with their feature; shared ones in `src/components/`.
- Each entry: name · purpose · key props · token usage. Add a one-line visual description.

## Components (to be built in Phase 5 — update as implemented)
- **StatusBadge** (`src/components/StatusBadge.tsx`) — shows document ingestion state.
  Props: `status: DocumentStatus`, `failedStage?: string | null`. Colors from tokens
  (`text-warning/border-warning` for the 4 non-terminal stages, `text-success/border-success`
  for READY, `text-danger/border-danger` for FAILED) — small bordered pill, `text-xs`,
  `rounded-sm`, `px-2 py-0.5`. FAILED additionally appends `(<failed stage label>)` inline.
  _[built, F51]_ — shared (not feature-folder-local) since F52/chat will likely reuse it.
- **UploadDropzone** — drag/drop or pick files; emits upload events; shows per-file progress.
  _[not built — F51 used a plain hidden `<input type="file">` + button instead (no drag/drop,
  no per-file progress, since the backend gives no upload-progress signal to show). Scope
  reduction, not a gap. Build the real dropzone later if drag/drop becomes a real ask.]_
- **FolderTree** (`src/features/documents/FolderTree.tsx`) — recursive nav of the folder tree,
  built client-side from the flat `parent_id` list returned by `GET /documents/folders` (no
  separate tree endpoint). Narrow, swappable props: `currentFolderId: string | null`,
  `onNavigate: (id: string | null) => void` — owns its own create/rename/move/delete UI
  internally, nothing else depends on those internals. Indentation via inline
  `paddingLeft: depth * 16 + 8`px (not a Tailwind spacing token — dynamic per-depth value,
  the 16/8 base numbers ARE the 4px-grid multiples though). Row: `text-sm`, `hover:bg-surface`,
  active row `bg-surface text-accent`. Inline action buttons (rename ✎ / delete × / move
  select) are `opacity-0 group-hover:opacity-100`, `text-muted`, hover to `text-text` (rename)
  or `text-danger` (delete). Every mutation invalidates the whole folder list (no optimistic
  patch) since F25's move/rename rebuild every descendant's `path` server-side.
  _[built, F51]_ — feature-folder-local (not reused outside `documents/`, no shared promotion).
- **NotebookSelector** — pick which documents are in scope for a chat. _[not built]_
- **ChatMessage** — renders a streamed answer with inline **citation markers** (mono); click →
  opens source span. Props: `role`, `content`, `citations[]`. _[not built]_
- **SourcePanel** — shows the cited chunk's text with `char_start/char_end` highlighted. _[not built]_
- **DebugBundle** (admin only) — collapsible: retrieved hits + scores, final prompt, raw output. _[not built]_

## Rule
Before creating a new component, check this list. Reuse or extend an existing one rather than
duplicating. Run **Imprint** to record anything new here.
