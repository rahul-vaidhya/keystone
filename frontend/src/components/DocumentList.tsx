import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient, type Query } from "@tanstack/react-query";
import { documentsApi } from "../services/documentsService";
import { useDialog } from "../hooks/useDialog";
import { ApiError } from "../types/auth";
import type { Document, Folder } from "../types/documents";
import { StatusBadge } from "./StatusBadge";
import { DocumentDetailModal, formatBytes, formatDateShort } from "./DocumentDetailModal";
import { DRAG_MIME, type DragPayload } from "./FolderTree";

const TERMINAL_STATUSES: ReadonlySet<Document["status"]> = new Set(["READY", "FAILED"]);

// Exported (not just inlined into the useQuery call) so it can be unit-tested directly
// against a list of documents without spinning up real timers.
export function pollIntervalFor(query: Query<Document[]>): number | false {
  const docs = query.state.data;
  if (!docs) return false;
  const hasNonTerminal = docs.some((d) => !TERMINAL_STATUSES.has(d.status));
  return hasNonTerminal ? 2000 : false;
}

// Mirrors the backend's upload validation (415 for anything but a PDF) so the user
// gets an immediate, friendly message instead of a round trip.
export const PDF_ONLY_MESSAGE = "Only PDF files can be uploaded. Please choose a .pdf file.";

export function isPdfFile(file: File): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

export function DocumentList({ currentFolderId }: { currentFolderId: string | null }) {
  const queryClient = useQueryClient();
  const dialog = useDialog();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedDoc, setSelectedDoc] = useState<Document | null>(null);
  // U11: a byte-identical re-upload returns the existing document (HTTP 200) —
  // tell the user instead of silently doing nothing.
  const [duplicateDoc, setDuplicateDoc] = useState<Document | null>(null);
  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());

  const documentsQuery = useQuery({
    queryKey: ["documents", { folderId: currentFolderId }],
    queryFn: () => documentsApi.listDocuments({ folderId: currentFolderId }),
    refetchInterval: pollIntervalFor,
  });

  // Same ["folders"] query key FolderTree uses — React Query dedupes this against
  // FolderTree's identical query when both are mounted, so this doesn't add a second
  // network round trip in the normal (FolderTree + DocumentList together) layout.
  const foldersQuery = useQuery({ queryKey: ["folders"], queryFn: documentsApi.listFolders });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => documentsApi.uploadDocument(file, currentFolderId),
    onMutate: () => setDuplicateDoc(null),
    onSuccess: ({ document, created }) => {
      if (!created) setDuplicateDoc(document);
      return queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to upload document"),
  });

  const deleteMutation = useMutation({
    mutationFn: (documentId: string) => documentsApi.deleteDocument(documentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to delete document"),
  });

  const moveMutation = useMutation({
    mutationFn: ({ documentId, folderId }: { documentId: string; folderId: string | null }) =>
      documentsApi.moveDocument(documentId, folderId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Failed to move document"),
  });

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!isPdfFile(file)) {
      void dialog.alert(PDF_ONLY_MESSAGE, { title: "Unsupported file type" });
      return;
    }
    uploadMutation.mutate(file);
  }

  // Scroll the already-existing document into view when it's in the current list.
  const duplicateInView =
    !!duplicateDoc && !!documentsQuery.data?.some((d) => d.id === duplicateDoc.id);
  useEffect(() => {
    if (duplicateInView && duplicateDoc) {
      rowRefs.current.get(duplicateDoc.id)?.scrollIntoView?.({ block: "nearest" });
    }
  }, [duplicateInView, duplicateDoc]);

  const folderName = (folderId: string | null) =>
    folderId === null
      ? "Repository root"
      : (foldersQuery.data ?? []).find((f) => f.id === folderId)?.name ?? "another folder";

  async function handleDelete(doc: Document) {
    const ok = await dialog.confirm(
      `Permanently delete "${doc.title}"? This cannot be undone.`,
      { confirmLabel: "Delete", danger: true },
    );
    if (!ok) return;
    deleteMutation.mutate(doc.id);
  }

  function handleDragStart(e: React.DragEvent, documentId: string) {
    const payload: DragPayload = { type: "document", id: documentId };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
  }

  // Keyboard/screen-reader/touch-accessible alternative to the drag-and-drop-only move
  // path above — always visible (never hover-gated), since the whole point is it must
  // work without a mouse.
  function handleMoveSelect(doc: Document, value: string) {
    const folderId = value === "" ? null : value;
    if (folderId === doc.folder_id) return; // no-op: selecting the current folder
    moveMutation.mutate({ documentId: doc.id, folderId });
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <main className="p-4 sm:p-6 max-w-5xl mx-auto w-full">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-lg font-semibold">Documents</h1>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadMutation.isPending}
            className="text-sm bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 disabled:opacity-50"
          >
            {uploadMutation.isPending ? "Uploading…" : "Upload PDF"}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,application/pdf"
            className="hidden"
            onChange={handleFileChange}
          />
        </div>

        {duplicateDoc && (
          <div
            role="status"
            className="mb-4 flex items-start gap-3 border border-accent rounded-md px-3 py-2 text-sm"
          >
            <p className="flex-1 min-w-0">
              <span className="font-medium">This file is already in your repository</span>
              <span className="text-muted">
                {" "}
                — &ldquo;<span className="break-all">{duplicateDoc.title}</span>&rdquo; in{" "}
                {folderName(duplicateDoc.folder_id)}.
              </span>
            </p>
            <button
              type="button"
              onClick={() => setSelectedDoc(duplicateDoc)}
              className="shrink-0 text-accent hover:underline"
            >
              View
            </button>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => setDuplicateDoc(null)}
              className="shrink-0 text-muted hover:text-text"
            >
              ×
            </button>
          </div>
        )}

        {documentsQuery.isLoading && <p className="text-muted text-sm">Loading…</p>}
        {documentsQuery.isError && (
          <p className="text-danger text-sm">Failed to load documents.</p>
        )}

        {documentsQuery.data && documentsQuery.data.length === 0 && (
          <p className="text-muted text-sm">No documents here yet. Upload a PDF to get started.</p>
        )}

        {documentsQuery.data && documentsQuery.data.length > 0 && (
          // overflow-x-auto (not overflow-hidden): if a row ever gets wider than the
          // container it scrolls instead of clipping the move/delete controls. Low-value
          // columns collapse at narrower breakpoints; on phones the status badge moves
          // under the title so title, status, move and delete all stay reachable.
          <div className="bg-surface border border-border rounded-lg overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted border-b border-border">
                  <th className="px-3 py-2 font-medium">Title</th>
                  <th className="px-3 py-2 font-medium hidden md:table-cell">Uploaded</th>
                  <th className="px-3 py-2 font-medium hidden lg:table-cell">Size</th>
                  <th className="px-3 py-2 font-medium hidden lg:table-cell">Pages</th>
                  <th className="px-3 py-2 font-medium hidden sm:table-cell">Status</th>
                  <th className="px-3 py-2 font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {documentsQuery.data.map((doc) => {
                  const isDuplicate = duplicateDoc?.id === doc.id;
                  const errorText =
                    doc.status === "FAILED" && doc.error_detail ? doc.error_detail : null;
                  return (
                    <tr
                      key={doc.id}
                      ref={(el) => {
                        if (el) rowRefs.current.set(doc.id, el);
                        else rowRefs.current.delete(doc.id);
                      }}
                      draggable
                      onDragStart={(e) => handleDragStart(e, doc.id)}
                      onClick={() => setSelectedDoc(doc)}
                      className={`group border-b border-border last:border-0 cursor-grab hover:bg-bg/50 align-top ${
                        isDuplicate ? "outline outline-2 -outline-offset-2 outline-accent" : ""
                      }`}
                    >
                      <td className="px-3 py-2 max-w-[11rem] sm:max-w-[16rem] lg:max-w-xs">
                        <div className="truncate" title={doc.title}>
                          {doc.title}
                        </div>
                        <div className="sm:hidden mt-1">
                          <StatusBadge status={doc.status} failedStage={doc.failed_stage} />
                          {errorText && (
                            <div className="text-muted text-xs truncate mt-0.5" title={errorText}>
                              {errorText}
                            </div>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap hidden md:table-cell">
                        <div>{formatDateShort(doc.created_at)}</div>
                        <div
                          className="text-xs text-muted truncate max-w-[12rem]"
                          title={doc.uploader_email ?? undefined}
                        >
                          {doc.uploader_email ?? "—"}
                        </div>
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap text-muted hidden lg:table-cell">
                        {formatBytes(doc.byte_size)}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap text-muted hidden lg:table-cell">
                        {doc.page_count ?? "—"}
                      </td>
                      <td className="px-3 py-2 hidden sm:table-cell max-w-[14rem]">
                        <StatusBadge status={doc.status} failedStage={doc.failed_stage} />
                        {errorText && (
                          <div className="text-muted text-xs truncate mt-0.5" title={errorText}>
                            {errorText}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1">
                          <select
                            aria-label={`Move ${doc.title} to folder`}
                            value={doc.folder_id ?? ""}
                            onChange={(e) => handleMoveSelect(doc, e.target.value)}
                            disabled={moveMutation.isPending}
                            className="max-w-[7.5rem] sm:max-w-[10rem] bg-bg border border-border rounded-sm text-xs text-muted px-1 py-0.5 disabled:opacity-50"
                          >
                            <option value="">Repository root</option>
                            {(foldersQuery.data ?? []).map((folder: Folder) => (
                              <option key={folder.id} value={folder.id}>
                                {folder.name}
                              </option>
                            ))}
                          </select>
                          {/* Always visible below lg (touch screens have no hover);
                              hover/focus-revealed on desktop. */}
                          <button
                            type="button"
                            aria-label={`Delete ${doc.title}`}
                            title="Delete"
                            onClick={() => void handleDelete(doc)}
                            disabled={deleteMutation.isPending}
                            className="lg:opacity-0 lg:group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 text-muted hover:text-danger px-1.5 text-base leading-none disabled:opacity-50"
                          >
                            ×
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <DocumentDetailModal document={selectedDoc} onClose={() => setSelectedDoc(null)} />
      </main>
    </div>
  );
}
