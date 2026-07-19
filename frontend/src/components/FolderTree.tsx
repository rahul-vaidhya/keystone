import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { documentsApi } from "../services/documentsService";
import { useAuth } from "../hooks/useAuth";
import { useDialog } from "../hooks/useDialog";
import { ApiError } from "../types/auth";
import type { Folder, FolderDeleteMode } from "../types/documents";
import { FolderDeleteDialog } from "./FolderDeleteDialog";

type TreeNode = Folder & { children: TreeNode[] };

// Drag payload set on dragstart (here, for folder rows; DocumentList sets the same
// shape for document rows) — a single small JSON envelope lets one onDrop handler
// branch on what's being dropped without a second drag protocol. Exported so
// DocumentList's drag source uses the exact same wire shape as this drop target.
export type DragPayload = { type: "folder"; id: string } | { type: "document"; id: string };
export const DRAG_MIME = "application/json";

function buildTree(folders: Folder[]): TreeNode[] {
  const byId = new Map<string, TreeNode>(folders.map((f) => [f.id, { ...f, children: [] }]));
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    if (node.parent_id && byId.has(node.parent_id)) {
      byId.get(node.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

// Descendant ids of `folderId` (via parent_id, mirroring the backend's BFS) — used to
// reject dropping a folder onto itself or one of its own descendants client-side (the
// backend also rejects this, FolderCycleError, but checking here avoids a round trip
// for the common case of a stray drop).
function descendantIds(folders: Folder[], folderId: string): Set<string> {
  const ids = new Set<string>([folderId]);
  let added = true;
  while (added) {
    added = false;
    for (const f of folders) {
      if (f.parent_id && ids.has(f.parent_id) && !ids.has(f.id)) {
        ids.add(f.id);
        added = true;
      }
    }
  }
  return ids;
}

// Narrow, swappable navigator: the rest of F51 only depends on this props contract, never
// on FolderTree's internals (client-side tree assembly, rename/delete UI, drag-and-drop).
export function FolderTree({
  currentFolderId,
  onNavigate,
}: {
  currentFolderId: string | null;
  onNavigate: (folderId: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const dialog = useDialog();
  const canManageTags = user?.role === "owner" || user?.role === "admin";
  const foldersQuery = useQuery({ queryKey: ["folders"], queryFn: documentsApi.listFolders });
  const tagsQuery = useQuery({ queryKey: ["tags"], queryFn: documentsApi.listTags });
  const [newFolderParent, setNewFolderParent] = useState<string | null | undefined>(undefined);
  const [newFolderName, setNewFolderName] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<TreeNode | null>(null);

  // Every folder mutation invalidates the whole ["folders"] list rather than patching a
  // single row optimistically: move/rename rebuild every descendant's `path`
  // server-side in one transaction, so a partial/optimistic update here would leave
  // descendant paths stale in the UI until the next unrelated refetch.
  const invalidateFolders = () => queryClient.invalidateQueries({ queryKey: ["folders"] });
  const invalidateDocuments = () => queryClient.invalidateQueries({ queryKey: ["documents"] });

  const createMutation = useMutation({
    mutationFn: ({ name, parentId }: { name: string; parentId: string | null }) =>
      documentsApi.createFolder(name, parentId),
    onSuccess: invalidateFolders,
  });

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      documentsApi.renameFolder(id, name),
    onSuccess: invalidateFolders,
  });

  const deleteMutation = useMutation({
    mutationFn: ({ id, mode }: { id: string; mode: FolderDeleteMode }) =>
      documentsApi.deleteFolder(id, mode),
    onSuccess: invalidateFolders,
  });

  const moveFolderMutation = useMutation({
    mutationFn: ({ id, parentId }: { id: string; parentId: string | null }) =>
      documentsApi.moveFolder(id, parentId),
    onSuccess: invalidateFolders,
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to move folder"),
  });

  const moveDocumentMutation = useMutation({
    mutationFn: ({ id, folderId }: { id: string; folderId: string | null }) =>
      documentsApi.moveDocument(id, folderId),
    onSuccess: invalidateDocuments,
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to move document"),
  });

  const tagFolderMutation = useMutation({
    mutationFn: ({ id, tagId }: { id: string; tagId: string }) =>
      documentsApi.tagFolder(id, tagId),
    onSuccess: invalidateFolders,
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to tag folder"),
  });

  const untagFolderMutation = useMutation({
    mutationFn: ({ id, tagId }: { id: string; tagId: string }) =>
      documentsApi.untagFolder(id, tagId),
    onSuccess: invalidateFolders,
  });

  const tree = useMemo(() => buildTree(foldersQuery.data ?? []), [foldersQuery.data]);
  const folders = foldersQuery.data ?? [];
  const tagName = (tagId: string) => tagsQuery.data?.find((t) => t.id === tagId)?.name ?? tagId;

  function handleCreateSubmit(parentId: string | null) {
    const name = newFolderName.trim();
    if (!name) return;
    createMutation.mutate(
      { name, parentId },
      {
        onError: (err) =>
          void dialog.alert(err instanceof ApiError ? err.message : "Failed to create folder"),
      },
    );
    setNewFolderName("");
    setNewFolderParent(undefined);
  }

  function handleRenameSubmit(id: string) {
    const name = renameValue.trim();
    if (!name) return;
    renameMutation.mutate(
      { id, name },
      {
        onError: (err) =>
          void dialog.alert(err instanceof ApiError ? err.message : "Failed to rename folder"),
      },
    );
    setRenamingId(null);
  }

  function runDelete(id: string, mode: FolderDeleteMode) {
    deleteMutation.mutate(
      { id, mode },
      {
        onError: (err) =>
          void dialog.alert(err instanceof ApiError ? err.message : "Failed to delete folder"),
      },
    );
  }

  async function handleDelete(folder: TreeNode) {
    const hasChildren = folder.children.length > 0;
    if (hasChildren) {
      // Bespoke dialog (not plain confirm) — a folder with contents needs a
      // cascade-vs-reflow choice, not a yes/no.
      setPendingDelete(folder);
      return;
    }
    const ok = await dialog.confirm(`Delete "${folder.name}"?`, {
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    runDelete(folder.id, "block");
  }

  function handleChooseDeleteMode(mode: "cascade" | "reflow") {
    if (!pendingDelete) return;
    const folderId = pendingDelete.id;
    setPendingDelete(null);
    runDelete(folderId, mode);
  }

  function handleDragStart(e: React.DragEvent, folderId: string) {
    const payload: DragPayload = { type: "folder", id: folderId };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
  }

  function handleDragOver(e: React.DragEvent, targetId: string | null) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverId(targetId ?? "__root__");
  }

  function handleDrop(e: React.DragEvent, targetFolderId: string | null) {
    e.preventDefault();
    setDragOverId(null);
    const raw = e.dataTransfer.getData(DRAG_MIME);
    if (!raw) return;
    let payload: DragPayload;
    try {
      payload = JSON.parse(raw) as DragPayload;
    } catch {
      return;
    }

    if (payload.type === "folder") {
      if (payload.id === targetFolderId) return; // no-op, dropped onto itself
      if (targetFolderId !== null && descendantIds(folders, payload.id).has(targetFolderId)) {
        void dialog.alert("Cannot move a folder into itself or one of its own subfolders.");
        return;
      }
      moveFolderMutation.mutate({ id: payload.id, parentId: targetFolderId });
    } else {
      moveDocumentMutation.mutate({ id: payload.id, folderId: targetFolderId });
    }
  }

  function handleToggleTag(folder: TreeNode, tagId: string, currentlyTagged: boolean) {
    if (currentlyTagged) {
      untagFolderMutation.mutate({ id: folder.id, tagId });
    } else {
      tagFolderMutation.mutate({ id: folder.id, tagId });
    }
  }

  function renderNode(node: TreeNode, depth: number) {
    const isActive = currentFolderId === node.id;
    const isDragOver = dragOverId === node.id;
    const grantableTags = (tagsQuery.data ?? []).filter((t) => !node.tag_ids.includes(t.id));
    return (
      <div key={node.id}>
        <div
          draggable
          onDragStart={(e) => handleDragStart(e, node.id)}
          onDragOver={(e) => handleDragOver(e, node.id)}
          onDragLeave={() => setDragOverId((prev) => (prev === node.id ? null : prev))}
          onDrop={(e) => handleDrop(e, node.id)}
          className={`group flex items-center gap-1 px-2 py-1 rounded-md text-sm hover:bg-surface cursor-grab ${
            isActive ? "bg-surface text-accent" : "text-text"
          } ${isDragOver ? "outline outline-2 outline-accent" : ""}`}
          style={{ paddingLeft: `${depth * 16 + 8}px` }}
        >
          {renamingId === node.id ? (
            <input
              autoFocus
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleRenameSubmit(node.id);
                if (e.key === "Escape") setRenamingId(null);
              }}
              onBlur={() => handleRenameSubmit(node.id)}
              className="flex-1 bg-bg border border-border rounded-sm px-1 text-sm"
            />
          ) : (
            <button
              type="button"
              title={node.name}
              onClick={() => onNavigate(node.id)}
              className="flex-1 text-left truncate"
            >
              {node.name}
            </button>
          )}
          {node.tag_ids.map((tagId) => (
            <span
              key={tagId}
              title={
                canManageTags
                  ? `Click to untag "${tagName(tagId)}"`
                  : `Tagged "${tagName(tagId)}"`
              }
              onClick={canManageTags ? () => handleToggleTag(node, tagId, true) : undefined}
              className={`text-[10px] border border-border rounded-sm px-1 text-muted ${
                canManageTags ? "cursor-pointer hover:border-danger hover:text-danger" : ""
              }`}
            >
              {tagName(tagId)}
            </span>
          ))}
          {canManageTags && grantableTags.length > 0 && (
            <select
              aria-label={`Tag ${node.name}`}
              value=""
              onChange={(e) => {
                if (!e.target.value) return;
                handleToggleTag(node, e.target.value, false);
                e.target.value = "";
              }}
              className="opacity-0 group-hover:opacity-100 bg-bg border border-border rounded-sm text-xs text-muted max-w-[70px]"
            >
              <option value="">+ tag</option>
              {grantableTags.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            aria-label={`Rename ${node.name}`}
            onClick={() => {
              setRenamingId(node.id);
              setRenameValue(node.name);
            }}
            className="opacity-0 group-hover:opacity-100 text-muted hover:text-text px-1"
          >
            ✎
          </button>
          <button
            type="button"
            aria-label={`Delete ${node.name}`}
            onClick={() => void handleDelete(node)}
            className="opacity-0 group-hover:opacity-100 text-muted hover:text-danger px-1"
          >
            ×
          </button>
        </div>
        {node.children.map((child) => renderNode(child, depth + 1))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        onClick={() => onNavigate(null)}
        onDragOver={(e) => handleDragOver(e, null)}
        onDragLeave={() => setDragOverId((prev) => (prev === "__root__" ? null : prev))}
        onDrop={(e) => handleDrop(e, null)}
        className={`text-left px-2 py-1 rounded-md text-sm hover:bg-surface ${
          currentFolderId === null ? "bg-surface text-accent" : "text-text"
        } ${dragOverId === "__root__" ? "outline outline-2 outline-accent" : ""}`}
      >
        All documents
      </button>

      {foldersQuery.isLoading && <p className="text-muted text-xs px-2">Loading…</p>}
      {foldersQuery.isError && <p className="text-danger text-xs px-2">Failed to load folders.</p>}

      {tree.map((node) => renderNode(node, 0))}

      {newFolderParent === undefined ? (
        <button
          type="button"
          onClick={() => setNewFolderParent(currentFolderId)}
          className="text-left px-2 py-1 rounded-md text-sm text-muted hover:text-text hover:bg-surface"
        >
          + New folder
        </button>
      ) : (
        <input
          autoFocus
          value={newFolderName}
          placeholder="Folder name"
          onChange={(e) => setNewFolderName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleCreateSubmit(newFolderParent ?? null);
            if (e.key === "Escape") setNewFolderParent(undefined);
          }}
          onBlur={() => handleCreateSubmit(newFolderParent ?? null)}
          className="bg-bg border border-border rounded-sm px-2 py-1 mx-2 text-sm"
        />
      )}

      <FolderDeleteDialog
        open={pendingDelete !== null}
        folderName={pendingDelete?.name ?? ""}
        onClose={() => setPendingDelete(null)}
        onChoose={handleChooseDeleteMode}
      />
    </div>
  );
}
