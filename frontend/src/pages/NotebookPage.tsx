import { useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { documentsApi } from "../services/documentsService";
import { notebooksApi } from "../services/notebooksService";
import { useDialog } from "../hooks/useDialog";
import { useAuth } from "../hooks/useAuth";
import { ApiError } from "../types/auth";
import type { Document } from "../types/documents";
import { StatusBadge } from "../components/StatusBadge";
import { ChatPanel } from "../components/ChatPanel";
import { NotebookOverviewPanel } from "../components/NotebookOverviewPanel";
import { ShareNotebookDialog } from "../components/ShareNotebookDialog";
import { RenameDialog } from "../components/RenameDialog";

export function NotebookPage() {
  const { notebookId } = useParams<{ notebookId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dialog = useDialog();
  const { user } = useAuth();
  const [shareOpen, setShareOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"chat" | "overview">("chat");

  const notebookQuery = useQuery({
    queryKey: ["notebooks", notebookId],
    queryFn: () => notebooksApi.get(notebookId!),
    enabled: !!notebookId,
  });

  const isOwner = !!user && notebookQuery.data?.created_by === user.id;

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
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to add document"),
  });

  const detachMutation = useMutation({
    mutationFn: (documentId: string) => notebooksApi.detachDocument(notebookId!, documentId),
    onSuccess: invalidateDocs,
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to remove document"),
  });

  const renameMutation = useMutation({
    mutationFn: (name: string) => notebooksApi.update(notebookId!, { name }),
    onSuccess: () => {
      setRenameOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["notebooks"] });
    },
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to rename notebook"),
  });

  const nbDocIds = new Set((nbDocsQuery.data ?? []).map((d: Document) => d.id));
  const addableDocs = (allDocsQuery.data ?? []).filter((d: Document) => !nbDocIds.has(d.id));

  if (!notebookId) return null;

  // U5: a private notebook you weren't shared on (403) or a bad id (404) gets a real
  // error state — never the endless "Loading…" + empty chat it used to fall through to.
  if (notebookQuery.isError) {
    const status = notebookQuery.error instanceof ApiError ? notebookQuery.error.status : 0;
    const [heading, body] =
      status === 403
        ? [
            "You don't have access to this notebook",
            "This notebook is private. Ask its owner to share it with you.",
          ]
        : status === 404
          ? ["Notebook not found", "It may have been deleted, or the link is incorrect."]
          : ["Couldn't load this notebook", "Something went wrong. Please try again."];
    return (
      <div className="flex-1 min-h-0 overflow-y-auto">
        <main className="p-6 max-w-md mx-auto w-full mt-12 text-center space-y-3" role="alert">
          <h1 className="text-lg font-semibold">{heading}</h1>
          <p className="text-sm text-muted">{body}</p>
          <Link to="/app/notebooks" className="inline-block text-sm text-accent hover:underline">
            ← Back to Notebooks
          </Link>
        </main>
      </div>
    );
  }

  return (
    <div className="flex flex-col lg:flex-row flex-1 min-h-0">
      {/* Left: document membership panel */}
      <aside className="w-full max-h-56 border-b lg:max-h-none lg:w-72 lg:border-b-0 lg:border-r shrink-0 border-border flex flex-col overflow-hidden">
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
            <div className="flex items-center justify-between gap-2">
              <h1 className="text-sm font-semibold truncate" title={notebookQuery.data?.name}>
                {notebookQuery.data?.name ?? "Notebook"}
              </h1>
              {isOwner ? (
                <div className="shrink-0 flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setRenameOpen(true)}
                    className="text-xs text-muted hover:text-text transition"
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    onClick={() => setShareOpen(true)}
                    className="text-xs text-accent hover:underline"
                  >
                    Share
                  </button>
                </div>
              ) : (
                <span className="shrink-0 text-xs text-muted">Shared with you</span>
              )}
            </div>
          )}
        </div>
        {isOwner && notebookQuery.data && (
          <RenameDialog
            open={renameOpen}
            title="Rename notebook"
            label="Notebook name"
            initialValue={notebookQuery.data.name}
            pending={renameMutation.isPending}
            onClose={() => setRenameOpen(false)}
            onSubmit={(name) => renameMutation.mutate(name)}
          />
        )}
        {isOwner && notebookQuery.data && (
          <ShareNotebookDialog
            open={shareOpen}
            notebookId={notebookQuery.data.id}
            notebookName={notebookQuery.data.name}
            onClose={() => setShareOpen(false)}
          />
        )}

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
                    {isOwner && (
                      <button
                        type="button"
                        aria-label={`Remove ${doc.title} from notebook`}
                        onClick={() => detachMutation.mutate(doc.id)}
                        disabled={detachMutation.isPending}
                        className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 text-muted hover:text-danger transition text-sm px-1"
                      >
                        ×
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Add from repository */}
          {isOwner && addableDocs.length > 0 && (
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
                      className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 text-accent text-xs hover:underline transition px-1 disabled:opacity-30"
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

      {/* Right: chat / overview tabs */}
      <div className="flex flex-col flex-1 min-h-0">
        <div className="shrink-0 border-b border-border flex gap-1 px-4">
          <button
            type="button"
            onClick={() => setActiveTab("chat")}
            className={`text-sm px-3 py-2 border-b-2 transition ${
              activeTab === "chat"
                ? "border-accent text-text"
                : "border-transparent text-muted hover:text-text"
            }`}
          >
            Chat
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("overview")}
            className={`text-sm px-3 py-2 border-b-2 transition ${
              activeTab === "overview"
                ? "border-accent text-text"
                : "border-transparent text-muted hover:text-text"
            }`}
          >
            Overview
          </button>
        </div>
        {/* Don't mount chat (and fire its history request) until the notebook itself
            has loaded — a 403/404 renders the error state above instead. */}
        {!notebookQuery.data ? (
          <p className="p-6 text-sm text-muted">Loading…</p>
        ) : activeTab === "chat" ? (
          <ChatPanel notebookId={notebookId} documents={nbDocsQuery.data ?? []} />
        ) : (
          <NotebookOverviewPanel notebookId={notebookId} documents={nbDocsQuery.data ?? []} />
        )}
      </div>
    </div>
  );
}
