import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, documentsApi, type Folder, type FolderDeleteMode } from "../../lib/api";

type TreeNode = Folder & { children: TreeNode[] };

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
// exclude self + descendants from the move target list, since the backend itself
// rejects moving a folder into its own subtree (FolderCycleError).
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
// on FolderTree's internals (client-side tree assembly, rename/move/delete UI).
export function FolderTree({
  currentFolderId,
  onNavigate,
}: {
  currentFolderId: string | null;
  onNavigate: (folderId: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const foldersQuery = useQuery({ queryKey: ["folders"], queryFn: documentsApi.listFolders });
  const [newFolderParent, setNewFolderParent] = useState<string | null | undefined>(undefined);
  const [newFolderName, setNewFolderName] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  // Every mutation invalidates the whole ["folders"] list rather than patching a single
  // row optimistically: move/rename rebuild every descendant's `path` server-side in one
  // transaction, so a partial/optimistic update here would leave descendant paths stale
  // in the UI until the next unrelated refetch. A full refetch always reflects the
  // freshly-rebuilt paths.
  const invalidateFolders = () => queryClient.invalidateQueries({ queryKey: ["folders"] });

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

  const moveMutation = useMutation({
    mutationFn: ({ id, parentId }: { id: string; parentId: string | null }) =>
      documentsApi.moveFolder(id, parentId),
    onSuccess: invalidateFolders,
  });

  const tree = useMemo(() => buildTree(foldersQuery.data ?? []), [foldersQuery.data]);
  const folders = foldersQuery.data ?? [];

  function handleCreateSubmit(parentId: string | null) {
    const name = newFolderName.trim();
    if (!name) return;
    createMutation.mutate(
      { name, parentId },
      {
        onError: (err) =>
          window.alert(err instanceof ApiError ? err.message : "Failed to create folder"),
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
          window.alert(err instanceof ApiError ? err.message : "Failed to rename folder"),
      },
    );
    setRenamingId(null);
  }

  function handleDelete(folder: TreeNode) {
    const hasChildren = folder.children.length > 0;
    let mode: FolderDeleteMode = "block";
    if (hasChildren) {
      const choice = window.prompt(
        `"${folder.name}" has folders/documents inside it. Type:\n` +
          `"cascade" to delete everything inside it\n` +
          `"reflow" to move its contents up a level and delete just this folder\n` +
          `(anything else cancels)`,
      );
      if (choice !== "cascade" && choice !== "reflow") return;
      mode = choice;
    } else if (!window.confirm(`Delete "${folder.name}"?`)) {
      return;
    }
    deleteMutation.mutate(
      { id: folder.id, mode },
      {
        onError: (err) =>
          window.alert(
            err instanceof ApiError ? err.message : "Failed to delete folder",
          ),
      },
    );
  }

  function moveTargetOptions(folder: TreeNode): { id: string | null; label: string }[] {
    const excluded = descendantIds(folders, folder.id);
    const options: { id: string | null; label: string }[] = folders
      .filter((f) => !excluded.has(f.id))
      .map((f) => ({ id: f.id as string | null, label: f.path }));
    if (folder.parent_id !== null) options.unshift({ id: null, label: "Root" });
    return options;
  }

  function handleMove(folder: TreeNode, targetParentId: string | null) {
    moveMutation.mutate(
      { id: folder.id, parentId: targetParentId },
      {
        onError: (err) =>
          window.alert(err instanceof ApiError ? err.message : "Failed to move folder"),
      },
    );
  }

  function renderNode(node: TreeNode, depth: number) {
    const isActive = currentFolderId === node.id;
    return (
      <div key={node.id}>
        <div
          className={`group flex items-center gap-1 px-2 py-1 rounded-md text-sm hover:bg-surface ${
            isActive ? "bg-surface text-accent" : "text-text"
          }`}
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
              onClick={() => onNavigate(node.id)}
              className="flex-1 text-left truncate"
            >
              {node.name}
            </button>
          )}
          <select
            aria-label={`Move ${node.name}`}
            value=""
            onChange={(e) => {
              if (!e.target.value) return;
              handleMove(node, e.target.value === "__root__" ? null : e.target.value);
              e.target.value = "";
            }}
            className="opacity-0 group-hover:opacity-100 bg-bg border border-border rounded-sm text-xs text-muted max-w-[60px]"
          >
            <option value="">⇲</option>
            {moveTargetOptions(node).map((opt) => (
              <option key={opt.id ?? "__root__"} value={opt.id ?? "__root__"}>
                {opt.label}
              </option>
            ))}
          </select>
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
            onClick={() => handleDelete(node)}
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
        className={`text-left px-2 py-1 rounded-md text-sm hover:bg-surface ${
          currentFolderId === null ? "bg-surface text-accent" : "text-text"
        }`}
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
    </div>
  );
}
