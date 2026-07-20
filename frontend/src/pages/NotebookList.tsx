import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { notebooksApi } from "../services/notebooksService";
import { useDialog } from "../hooks/useDialog";
import { ApiError } from "../types/auth";

export function NotebookList() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dialog = useDialog();
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  const notebooksQuery = useQuery({
    queryKey: ["notebooks"],
    queryFn: notebooksApi.list,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["notebooks"] });

  const createMutation = useMutation({
    mutationFn: (name: string) => notebooksApi.create(name, null),
    onSuccess: invalidate,
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to create notebook"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => notebooksApi.delete(id),
    onSuccess: invalidate,
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to delete notebook"),
  });

  function handleCreate() {
    const name = newName.trim();
    if (!name) return;
    createMutation.mutate(name);
    setNewName("");
    setCreating(false);
  }

  async function handleDelete(e: React.MouseEvent, id: string, name: string) {
    e.stopPropagation();
    const ok = await dialog.confirm(`Delete notebook "${name}"?`, {
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    deleteMutation.mutate(id);
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <main className="p-6 max-w-2xl mx-auto w-full">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-lg font-semibold">Notebooks</h1>
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90"
          >
            New notebook
          </button>
        </div>

        {creating && (
          <div className="mb-4 flex gap-2">
            <input
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreate();
                if (e.key === "Escape") { setCreating(false); setNewName(""); }
              }}
              placeholder="Notebook name"
              className="flex-1 bg-bg border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
            />
            <button
              type="button"
              onClick={handleCreate}
              disabled={createMutation.isPending}
              className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
            >
              Create
            </button>
            <button
              type="button"
              onClick={() => { setCreating(false); setNewName(""); }}
              className="text-sm border border-border rounded-md px-3 py-1.5 text-muted hover:text-text hover:bg-surface"
            >
              Cancel
            </button>
          </div>
        )}

        {notebooksQuery.isLoading && <p className="text-muted text-sm">Loading…</p>}
        {notebooksQuery.isError && (
          <p className="text-danger text-sm">Failed to load notebooks.</p>
        )}

        {notebooksQuery.data && notebooksQuery.data.length === 0 && !creating && (
          <p className="text-muted text-sm">
            No notebooks yet. Create one to start asking questions about your documents.
          </p>
        )}

        {notebooksQuery.data && notebooksQuery.data.length > 0 && (
          <div className="space-y-2">
            {notebooksQuery.data.map((nb) => (
              <div
                key={nb.id}
                onClick={() => navigate(`/app/notebooks/${nb.id}`)}
                className="group flex items-center gap-3 bg-surface border border-border rounded-lg px-4 py-3 cursor-pointer hover:border-accent transition"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{nb.name}</p>
                  {nb.description && (
                    <p className="text-xs text-muted truncate mt-0.5">{nb.description}</p>
                  )}
                </div>
                <button
                  type="button"
                  aria-label={`Delete ${nb.name}`}
                  onClick={(e) => void handleDelete(e, nb.id, nb.name)}
                  className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 text-muted hover:text-danger transition p-1 rounded"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
