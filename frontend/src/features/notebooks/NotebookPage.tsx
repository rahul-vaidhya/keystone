import { useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, notebooksApi, documentsApi, type Document } from "../../lib/api";
import { StatusBadge } from "../../components/StatusBadge";
import { ChatPanel } from "../chat/ChatPanel";

export function NotebookPage() {
  const { notebookId } = useParams<{ notebookId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const notebookQuery = useQuery({
    queryKey: ["notebooks", notebookId],
    queryFn: () => notebooksApi.get(notebookId!),
    enabled: !!notebookId,
  });

  const nbDocsQuery = useQuery({
    queryKey: ["notebooks", notebookId, "documents"],
    queryFn: () => notebooksApi.listDocuments(notebookId!),
    enabled: !!notebookId,
  });

  const allDocsQuery = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.listDocuments(),
  });

  const invalidateDocs = () =>
    queryClient.invalidateQueries({ queryKey: ["notebooks", notebookId, "documents"] });

  const attachMutation = useMutation({
    mutationFn: (documentId: string) => notebooksApi.attachDocument(notebookId!, documentId),
    onSuccess: invalidateDocs,
    onError: (err) =>
      window.alert(err instanceof ApiError ? err.message : "Failed to add document"),
  });

  const detachMutation = useMutation({
    mutationFn: (documentId: string) => notebooksApi.detachDocument(notebookId!, documentId),
    onSuccess: invalidateDocs,
    onError: (err) =>
      window.alert(err instanceof ApiError ? err.message : "Failed to remove document"),
  });

  const nbDocIds = new Set((nbDocsQuery.data ?? []).map((d: Document) => d.id));
  const addableDocs = (allDocsQuery.data ?? []).filter((d: Document) => !nbDocIds.has(d.id));

  if (!notebookId) return null;

  return (
    <div className="flex flex-1 min-h-0">
      {/* Left: document membership panel */}
      <aside className="w-72 shrink-0 border-r border-border flex flex-col overflow-hidden">
        <div className="shrink-0 px-4 py-3 border-b border-border">
          <button
            type="button"
            onClick={() => navigate("/app/notebooks")}
            className="text-xs text-muted hover:text-text transition mb-1"
          >
            ← Notebooks
          </button>
          {notebookQuery.isLoading ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : (
            <h1 className="text-sm font-semibold truncate" title={notebookQuery.data?.name}>
              {notebookQuery.data?.name ?? "Notebook"}
            </h1>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {/* Documents in this notebook */}
          <div className="px-3 pt-3 pb-1">
            <p className="text-xs text-muted uppercase tracking-wide mb-2">In this notebook</p>
            {nbDocsQuery.isLoading && <p className="text-xs text-muted px-1">Loading…</p>}
            {nbDocsQuery.data && nbDocsQuery.data.length === 0 && (
              <p className="text-xs text-muted px-1">
                No documents yet. Add some from the repository below.
              </p>
            )}
            {nbDocsQuery.data && nbDocsQuery.data.length > 0 && (
              <div className="space-y-1">
                {nbDocsQuery.data.map((doc: Document) => (
                  <div
                    key={doc.id}
                    className="group flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-surface"
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-xs truncate" title={doc.title}>
                        {doc.title}
                      </p>
                      <StatusBadge status={doc.status} failedStage={doc.failed_stage} />
                    </div>
                    <button
                      type="button"
                      aria-label={`Remove ${doc.title} from notebook`}
                      onClick={() => detachMutation.mutate(doc.id)}
                      disabled={detachMutation.isPending}
                      className="opacity-0 group-hover:opacity-100 text-muted hover:text-danger transition text-sm px-1"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Add from repository */}
          {addableDocs.length > 0 && (
            <div className="px-3 pt-3 pb-3 border-t border-border mt-2">
              <p className="text-xs text-muted uppercase tracking-wide mb-2">
                Add from repository
              </p>
              <div className="space-y-1">
                {addableDocs.map((doc: Document) => (
                  <div
                    key={doc.id}
                    className="group flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-surface"
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-xs truncate" title={doc.title}>
                        {doc.title}
                      </p>
                      <StatusBadge status={doc.status} failedStage={doc.failed_stage} />
                    </div>
                    <button
                      type="button"
                      aria-label={`Add ${doc.title} to notebook`}
                      onClick={() => attachMutation.mutate(doc.id)}
                      disabled={attachMutation.isPending || doc.status !== "READY"}
                      className="opacity-0 group-hover:opacity-100 text-accent text-xs hover:underline transition px-1 disabled:opacity-30"
                    >
                      + Add
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* Right: chat panel */}
      <ChatPanel notebookId={notebookId} documents={nbDocsQuery.data ?? []} />
    </div>
  );
}
