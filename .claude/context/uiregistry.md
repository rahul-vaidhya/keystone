# uiregistry.md — Component Catalogue (LEAN)

> Populated by the **Imprint** skill after building any reusable component, to prevent
> duplicates and drift. Seeded below with the components Phase 5 will introduce; fill in
> real prop/class details as you build them.

## Conventions
- Screen pages (routed, area-specific) live in `src/pages/` (e.g. `HomePage.tsx`, `DocumentsPage.tsx`,
  `NotebookPage.tsx`). Shared non-routed UI lives in `src/components/` (e.g. `StatusBadge.tsx`,
  `FolderTree.tsx`, `ChatPanel.tsx`). Layouts in `src/layouts/` (e.g. `AppShell.tsx`, `Sidebar.tsx`).
- Each entry: name · purpose · key props · token usage. Add a one-line visual description.

## Components (to be built in Phase 5 — update as implemented)
- **StatusBadge** (`src/components/StatusBadge.tsx`) — shows document ingestion state.
  Props: `status: DocumentStatus`, `failedStage?: string | null`. Colors from tokens
  (`text-warning/border-warning` for the 4 non-terminal stages, `text-success/border-success`
  for READY, `text-danger/border-danger` for FAILED) — small bordered pill, `text-xs`,
  `rounded-sm`, `px-2 py-0.5`. FAILED additionally appends `(<failed stage label>)` inline.
  _[built, F51]_ — shared (not area-local) since F52/chat will likely reuse it.
- **UploadDropzone** — drag/drop or pick files; emits upload events; shows per-file progress.
  _[not built — F51 used a plain hidden `<input type="file">` + button instead (no drag/drop,
  no per-file progress, since the backend gives no upload-progress signal to show). Scope
  reduction, not a gap. Build the real dropzone later if drag/drop becomes a real ask.]_
- **FolderTree** (`src/components/FolderTree.tsx`) — recursive nav of the folder tree,
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
  _[built, F51]_ — shared component (not page-local) in `src/components/`.
- **NotebookSelector** — pick which documents are in scope for a chat. _[not built — replaced by
  the document membership panel inside `NotebookPage` which is always visible in the left rail]_
- **ChatMessage** — renders a streamed answer with inline citation markers. _[built as part of
  `ChatPanel` in `src/components/ChatPanel.tsx` (private sub-component, not a separate export).
  See ChatPanel entry below.]_
- **SourcePanel** — shows the cited chunk's text. _[built as `CitationPanel` in `src/components/CitationPanel.tsx`.
  See entry below.]_
- **DebugBundle** (admin only) — collapsible: retrieved hits + scores, final prompt, raw output. _[not built — F42]_

---

### NotebookList

File: `src/pages/NotebookList.tsx`
Last updated: 2026-07-02 (single-MVC refactor)

| Property       | Class                                                    |
| -------------- | -------------------------------------------------------- |
| Background     | `bg-surface` (notebook cards)                            |
| Border         | `border border-border`; hover → `hover:border-accent`    |
| Border radius  | `rounded-lg` (notebook cards), `rounded-md` (inputs/btns)|
| Text — primary | `text-sm font-medium` (notebook name)                    |
| Text — secondary | `text-xs text-muted` (description, empty-state)        |
| Spacing        | `p-6` page padding; `px-4 py-3` card padding; `mb-6` heading gap |
| Hover state    | `hover:border-accent transition` on card; `hover:opacity-90` on primary button |
| Accent usage   | `bg-accent text-white` primary button; `hover:border-accent` card hover |
| Delete button  | `opacity-0 group-hover:opacity-100 text-muted hover:text-danger` — matches FolderTree row action pattern |

**Pattern notes:**
Card rows use `group` + `opacity-0 group-hover:opacity-100` for action buttons — same convention as FolderTree. Navigation on card click (full card is clickable). Primary action button uses `bg-accent text-white rounded-md px-3 py-1.5 text-sm hover:opacity-90`. Secondary/cancel button uses `border border-border rounded-md px-3 py-1.5 text-muted hover:text-text hover:bg-surface`.

---

### NotebookPage

File: `src/pages/NotebookPage.tsx`
Last updated: 2026-07-02 (single-MVC refactor)

| Property       | Class                                                    |
| -------------- | -------------------------------------------------------- |
| Left panel bg  | inherits `bg-bg`; border: `border-r border-border`       |
| Section headers | `text-xs text-muted uppercase tracking-wide`            |
| Document rows  | `px-2 py-1.5 rounded-md hover:bg-surface group`          |
| Text — title   | `text-xs truncate`                                       |
| Spacing        | `px-3 pt-3 pb-1` section; `px-4 py-3` header            |
| Divider        | `border-t border-border mt-2`                            |
| Add button     | `opacity-0 group-hover:opacity-100 text-accent text-xs hover:underline` |
| Remove button  | `opacity-0 group-hover:opacity-100 text-muted hover:text-danger` |

**Pattern notes:**
Left panel is `w-72 shrink-0 border-r border-border`. Section labels in all-caps `text-xs text-muted uppercase tracking-wide` used to separate "In this notebook" from "Add from repository". Document rows mirror FolderTree's `group` + hover-reveal action button pattern. Only READY documents can be added (disabled button for non-READY).

---

### ChatPanel

File: `src/components/ChatPanel.tsx`
Last updated: 2026-07-02 (single-MVC refactor)

| Property            | Class                                                          |
| ------------------- | -------------------------------------------------------------- |
| User bubble bg      | `bg-accent text-white rounded-lg px-4 py-3 text-sm`           |
| Assistant bubble bg | `bg-surface border border-border text-text rounded-lg px-4 py-3 text-sm` |
| Bubble max-width    | `max-w-[80%]`                                                  |
| Typing indicator    | `text-muted animate-pulse` (●●●)                               |
| Citation marker     | `font-mono text-accent text-xs hover:underline align-super px-0.5` |
| Input area          | `shrink-0 border-t border-border p-4`                          |
| Input field         | `bg-bg border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent` |
| Submit button       | `bg-accent text-white rounded-md px-4 py-2 text-sm hover:opacity-90 disabled:opacity-50` |
| Messages container  | `flex-1 overflow-y-auto p-4 space-y-4`                         |

**Pattern notes:**
Chat bubbles: user on right (`justify-end`), assistant on left (`justify-start`). Citation markers (`[n]`) split from plain text via regex and rendered as `<button>` elements inline — clicking sets `activeCitation` to open `CitationPanel`. Streaming state: while `isStreaming && content === ""`, shows `●●●` typing indicator. After `onDone`, citations arrive and markers become clickable. Stream is aborted via `AbortController` stored in `abortRef` on unmount or new submission.

---

### CitationPanel

File: `src/components/CitationPanel.tsx`
Last updated: 2026-07-02 (single-MVC refactor)

| Property       | Class                                                |
| -------------- | ---------------------------------------------------- |
| Panel width    | `w-80` (set by parent `ChatPanel`)                   |
| Header         | `px-4 py-3 border-b border-border`                   |
| Header text    | `text-sm font-medium`                                |
| Close button   | `text-muted hover:text-text transition p-1 rounded`  |
| Body           | `flex-1 overflow-y-auto p-4 space-y-3`               |
| Document label | `text-xs text-muted truncate`                        |
| Offset label   | `font-mono text-xs text-muted`                       |
| Source quote   | `border-l-2 border-accent pl-3 text-sm text-text whitespace-pre-wrap leading-relaxed` |

**Pattern notes:**
CitationPanel is a `SourcePanel`-equivalent (was planned). Lives in `src/components/` as a shared component.
The source quote uses a left-accent-colored border (`border-l-2 border-accent`) and `font-mono` for the
offset label — citations are always displayed in mono font per `uitokens.md`. Panel appears as a third column
(`w-80 border-l border-border`) inside `ChatPanel` when a citation is active.

## Rule
Before creating a new component, check this list. Reuse or extend an existing one rather than
duplicating. Run **Imprint** to record anything new here.
