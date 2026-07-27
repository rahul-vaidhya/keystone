import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Modal } from "./Modal";
import { notebooksApi } from "../services/notebooksService";
import { authApi } from "../services/authService";
import { useDialog } from "../hooks/useDialog";
import { ApiError } from "../types/auth";

/**
 * Creator-only. Grants/revokes view+chat access to specific org members — a notebook
 * is private by default (see backend `KnowledgeService._fetch_visible`); this is the
 * only UI surface that changes who else can see one.
 */
export function ShareNotebookDialog({
  open,
  notebookId,
  notebookName,
  onClose,
}: {
  open: boolean;
  notebookId: string;
  notebookName: string;
  onClose: () => void;
}) {
  const titleId = useId();
  const queryClient = useQueryClient();
  const dialog = useDialog();
  const [selectedUserId, setSelectedUserId] = useState("");

  const sharesQuery = useQuery({
    queryKey: ["notebooks", notebookId, "shares"],
    queryFn: () => notebooksApi.listShares(notebookId),
    enabled: open,
  });
  const usersQuery = useQuery({
    queryKey: ["users"],
    queryFn: authApi.listUsers,
    enabled: open,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["notebooks", notebookId, "shares"] });

  const shareMutation = useMutation({
    mutationFn: (userId: string) => notebooksApi.share(notebookId, userId),
    onSuccess: () => {
      setSelectedUserId("");
      invalidate();
    },
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to share notebook"),
  });

  const unshareMutation = useMutation({
    mutationFn: (userId: string) => notebooksApi.unshare(notebookId, userId),
    onSuccess: invalidate,
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to remove access"),
  });

  const sharedIds = new Set((sharesQuery.data ?? []).map((s) => s.user_id));
  const candidates = (usersQuery.data ?? []).filter((u) => !sharedIds.has(u.id));

  return (
    <Modal open={open} onClose={onClose} titleId={titleId}>
      <h2 id={titleId} className="text-base font-semibold">
        Share &quot;{notebookName}&quot;
      </h2>
      <p className="text-sm text-muted">
        People you share with can view this notebook and ask questions. Only you can
        rename it, delete it, or manage sharing.
      </p>

      {candidates.length > 0 && (
        <div className="flex gap-2">
          <select
            aria-label="Select a member to share with"
            value={selectedUserId}
            onChange={(e) => setSelectedUserId(e.target.value)}
            className="flex-1 bg-bg border border-border rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-accent"
          >
            <option value="">Select a member…</option>
            {candidates.map((u) => (
              <option key={u.id} value={u.id}>
                {u.email}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={!selectedUserId || shareMutation.isPending}
            onClick={() => shareMutation.mutate(selectedUserId)}
            className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
          >
            Share
          </button>
        </div>
      )}

      <div className="space-y-1">
        <p className="text-xs text-muted uppercase tracking-wide">Shared with</p>
        {sharesQuery.data && sharesQuery.data.length === 0 && (
          <p className="text-sm text-muted">Not shared with anyone yet.</p>
        )}
        {sharesQuery.data && sharesQuery.data.length > 0 && (
          <ul className="space-y-1 max-h-48 overflow-y-auto">
            {sharesQuery.data.map((s) => (
              <li
                key={s.user_id}
                className="flex items-center justify-between px-2 py-1.5 rounded-md bg-bg"
              >
                <span className="text-sm truncate">{s.email}</span>
                <button
                  type="button"
                  aria-label={`Remove ${s.email}`}
                  onClick={() => unshareMutation.mutate(s.user_id)}
                  disabled={unshareMutation.isPending}
                  className="text-xs text-muted hover:text-danger transition"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex justify-end pt-2">
        <button
          type="button"
          onClick={onClose}
          className="text-sm text-muted hover:text-text px-2 py-1.5"
        >
          Close
        </button>
      </div>
    </Modal>
  );
}
