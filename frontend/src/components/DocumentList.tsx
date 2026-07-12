import { useRef } from "react";
import { useMutation, useQuery, useQueryClient, type Query } from "@tanstack/react-query";
import { documentsApi } from "../services/documentsService";
import { ApiError } from "../types/auth";
import type { Document } from "../types/documents";
import { StatusBadge } from "./StatusBadge";

const TERMINAL_STATUSES: ReadonlySet<Document["status"]> = new Set(["READY", "FAILED"]);

// Exported (not just inlined into the useQuery call) so it can be unit-tested directly
// against a list of documents without spinning up real timers.
export function pollIntervalFor(query: Query<Document[]>): number | false {
  const docs = query.state.data;
  if (!docs) return false;
  const hasNonTerminal = docs.some((d) => !TERMINAL_STATUSES.has(d.status));
  return hasNonTerminal ? 2000 : false;
}

export function DocumentList({ currentFolderId }: { currentFolderId: string | null }) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const documentsQuery = useQuery({
    queryKey: ["documents", { folderId: currentFolderId }],
    queryFn: () => documentsApi.listDocuments({ folderId: currentFolderId }),
    refetchInterval: pollIntervalFor,
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => documentsApi.uploadDocument(file, currentFolderId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
    onError: (err) =>
      window.alert(err instanceof ApiError ? err.message : "Failed to upload document"),
  });

  const deleteMutation = useMutation({
    mutationFn: (documentId: string) => documentsApi.deleteDocument(documentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
    onError: (err) =>
      window.alert(err instanceof ApiError ? err.message : "Failed to delete document"),
  });

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) uploadMutation.mutate(file);
    e.target.value = "";
  }

  function handleDelete(doc: Document) {
    if (!window.confirm(`Permanently delete "${doc.title}"? This cannot be undone.`)) return;
    deleteMutation.mutate(doc.id);
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <main className="p-6 max-w-3xl mx-auto w-full">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-lg font-semibold">Documents</h1>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadMutation.isPending}
            className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
          >
            {uploadMutation.isPending ? "Uploading…" : "Upload"}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={handleFileChange}
          />
        </div>

        {documentsQuery.isLoading && <p className="text-muted text-sm">Loading…</p>}
        {documentsQuery.isError && (
          <p className="text-danger text-sm">Failed to load documents.</p>
        )}

        {documentsQuery.data && documentsQuery.data.length === 0 && (
          <p className="text-muted text-sm">No documents here yet. Upload one to get started.</p>
        )}

        {documentsQuery.data && documentsQuery.data.length > 0 && (
          <div className="bg-surface border border-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted border-b border-border">
                  <th className="px-4 py-2 font-medium">Title</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium w-8" />
                </tr>
              </thead>
              <tbody>
                {documentsQuery.data.map((doc) => (
                  <tr key={doc.id} className="group border-b border-border last:border-0">
                    <td className="px-4 py-2 truncate max-w-xs">{doc.title}</td>
                    <td className="px-4 py-2">
                      <StatusBadge status={doc.status} failedStage={doc.failed_stage} />
                      {doc.status === "FAILED" && doc.error_detail && (
                        <span className="text-muted text-xs ml-2">{doc.error_detail}</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        type="button"
                        aria-label={`Delete ${doc.title}`}
                        onClick={() => handleDelete(doc)}
                        disabled={deleteMutation.isPending}
                        className="opacity-0 group-hover:opacity-100 text-muted hover:text-danger px-1 disabled:opacity-50"
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
