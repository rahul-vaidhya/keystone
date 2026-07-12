# Implementation Plan — Access Roles (tag-based RBAC) + Folder Tree Drag-and-Drop

> Produced via the `/architect` skill, 2026-07-12. Confirmed by the user — ready to build.
> This supersedes the "folder-based access restriction" feature from
> `docs/document-delete-folder-restriction-plan.md` (same session, earlier commit
> `7012af2`) — that mechanism is being fully removed, not kept alongside this one.

## What we are building

A new **Access Role** system replacing the `folders.restricted` boolean shipped in the
prior commit: org owners/admins create named Access Roles (e.g. "Finance Team"), assign
org members to them, and grant tags to them. Any folder or document carrying a tag
that's been granted to at least one Access Role becomes gated — visible only to
owner/admin and members holding a role with that tag; everything else stays open to the
whole org, exactly as today. Folder tags cascade down the subtree (like `restricted`
did); document tags apply directly. Separately, the folder tree gets real drag-and-drop
(folders reparenting, documents moving into folders) using the native HTML5 DnD API,
replacing the dropdown-based move UI.

## Why now

Direct follow-up ask after the prior session's simple owner/admin-vs-member folder
restriction: the user wants org owners to define custom roles, assign members to them,
and control access via tags on folders/files rather than a single binary restricted
flag. Also flagged the current folder-move UX (a `<select>` dropdown) as unintuitive
and asked for drag-and-drop, researched against current best practice rather than
guessed.

## Language agreed on

- **Access Role**: a new, separate concept from the existing system role (owner/admin/
  member — DB CHECK-constrained, migration `0003`, governs invites/role-changes). A
  user can hold zero or more Access Roles in addition to their one system role.
- **Tag**: the existing org-wide `tags` table (already used for organizing documents via
  `document_tags`), reused for both organization AND — once a tag is granted to an
  Access Role — access control.
- **Access-controlling tag**: a tag becomes this the moment any Access Role is granted
  it. Untagged, or tagged-with-a-never-granted-tag, resources stay open to everyone (no
  behavior change from today).
- **Gated resource**: a folder or document carrying an access-controlling tag (directly,
  or inherited from an ancestor folder for documents/subfolders).

## Decisions made

- **System role and Access Role are fully separate** — owner/admin/member keeps
  governing invites/role-changes; Access Roles are purely about resource visibility.
  Doesn't touch `auth.py`'s existing CHECK-constrained role logic at all.
- **Tags are dual-purpose** (reused, not a separate permission-tag concept). **Named
  risk, accepted**: because document-tagging stays open to any member (unchanged from
  today), a plain member CAN accidentally make a document invisible to most of the org
  by attaching a tag that happens to already be granted to some Access Role. Direct
  consequence of "tag reuse" + "document-tagging stays permission-free" — flagged
  explicitly, not hidden.
- **Multiple Access Roles per member** — standard RBAC, a person can be on more than one
  team's role.
- **Folder-tagging is admin/owner-only**; document-tagging stays open to any member
  (unchanged). A folder tag cascades to an entire subtree, so tagging a folder is the
  higher-stakes action.
- **Drag-and-drop via native HTML5 DnD API** — zero new dependency, matches this
  codebase's zero-UI-library style (only react-query/react-router-dom exist today).
  Covers both folder reparenting (replacing the `<select>` dropdown) and dragging a
  document onto a folder to move it. (Considered and rejected: `react-complex-tree` —
  W3C-accessible-tree-spec compliant, better built-in accessibility, but a real new
  dependency and a rewrite of the hand-rolled tree.)
- **`folders.restricted` is fully removed**, not deprecated — column dropped, the
  inheritance-walk code, the admin-only restriction endpoint, and the FolderTree
  lock/unlock button all deleted in this change. Pre-production software (no real
  customer data yet), so no migration-safety concern about losing that column's data.
- **New backend capability required and in scope**: there is currently NO way to move a
  document to a different folder after upload (`folder_id` set once, at upload time) —
  dragging a document onto a folder needs a new `PATCH /documents/{id}/folder`
  endpoint. Not explicitly asked for but required to make "drag a document into a
  folder" work at all.

## Assumptions

- Access Role management (create/delete role, grant/revoke tags, assign/remove members)
  is admin/owner-only, mirroring the existing invite/change-role gating.
- No invite-UI is being built in this pass — assigning Access Roles is scoped to
  *existing* org members (extends the current `UsersPage.tsx` list), not adding a way to
  invite new users from the UI.
- New domain `access_roles` (models/routes/controllers/services) — cross-references
  both `auth` (users) and `documents` (tags, folders), doesn't naturally belong to
  either. Follows the module-boundary rule (cross-domain calls only through
  `documents_service`/`auth_service`).
- Next migration after this is `0013` (this feature claims `0012`).

## How to build it

1. **Migration `0012`**: new tables `access_roles` (org_id, name, unique per org),
   `user_access_roles` (user_id, access_role_id, org_id), `access_role_tags`
   (access_role_id, tag_id, org_id), `folder_tags` (folder_id, tag_id, org_id — mirrors
   existing `document_tags`); `DROP COLUMN folders.restricted`.
2. **`app/models/access_roles.py`**: `AccessRole`, `UserAccessRole`, `AccessRoleTag` ORM
   models + API schemas. Add `FolderTag` to `app/models/documents.py` (same file as
   `Tag`/`DocumentTag`).
3. **`app/services/access_roles.py`**: repository + service — create/list/delete role,
   grant/revoke tag to role, assign/remove user to role, and a narrow accessor
   `resolve_user_granted_tags(ctx) -> set[tag_id]` for retrieval to call.
4. **`app/services/documents/tags.py`** (or a new file beside it): `tag_folder`/
   `untag_folder`, admin-gated in the controller.
5. **`app/services/documents/documents.py`**: new `move_document(ctx, document_id,
   folder_id)` for the drag-and-drop target.
6. **`app/services/retrieval.py`**: rewrite `resolve_allowed_documents` — owner/admin
   bypass unchanged; for members, compute each folder's inherited effective tag set
   (top-down pass, same shape as the old `_inherited_restricted_ids` but accumulating
   tag-id sets), fetch org-wide access-controlling tags + this user's granted tags via
   `access_roles_service`, then allow a document if its effective tag set (own tags ∪
   inherited folder tags) has no access-controlling tag, or has one the user's roles
   grant.
7. **Routes/controllers**: `POST/GET/DELETE /access-roles`, `POST/DELETE
   /access-roles/{id}/tags/{tag_id}`, `POST/DELETE /access-roles/{id}/users/{user_id}`,
   `POST/DELETE /documents/folders/{id}/tags/{tag_id}`, `PATCH /documents/{id}/folder`.
   Remove `PATCH /documents/folders/{id}/restriction`.
8. **Frontend**: new `services/accessRolesService.ts` + types; new
   `pages/AccessRolesPage.tsx` (create role, manage its tags, assign/remove members —
   cross-references the existing users list); `FolderTree.tsx` loses the lock/unlock
   button, gains tag badges + an admin-only tag-manager affordance, and native
   `draggable`/`onDragOver`/`onDrop` handlers on folder rows; `DocumentList.tsx` rows
   become draggable and drop-targetable onto `FolderTree` folders.
9. **Tests**: backend — role/tag CRUD, grant/revoke, the rewritten
   `resolve_allowed_documents` (untagged open, tagged-but-ungranted open,
   tagged-and-granted restricted-to-role-holders, folder-tag inheritance, admin bypass,
   folder-tagging requires admin, document-tagging stays open); frontend —
   drag-and-drop interaction tests, Access Role page tests.
10. Update `architecture.md`'s permission-model note; correct the "folder is not a
    permission boundary" line again (superseding the prior session's edit) if needed.

## Definition of Done

- Owner/admin can create an Access Role, grant it tags, and assign/remove org members
  to/from it.
- A member holding a role granted tag `T` can see any folder/document tagged (directly
  or via folder-inheritance) with `T`; a member without any role granting `T` cannot.
- An untagged folder/document, or one tagged only with tags no role has ever been
  granted, remains visible to every org member — zero behavior change from before this
  feature for the common case.
- `folders.restricted`, its endpoint, and its UI are fully removed — no dead code left
  behind.
- Folder reparenting and document-to-folder moves both work via native HTML5
  drag-and-drop in the browser; the old `<select>`-based move UI is gone.
- Full suite green (backend pytest + ruff, frontend vitest + tsc + build), independent
  review pass clean against the 8 hard rules in `orchestrator.md`.
