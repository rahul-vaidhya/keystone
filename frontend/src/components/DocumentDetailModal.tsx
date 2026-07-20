import { useId } from "react";
import type { Document } from "../types/documents";
import { Modal } from "./Modal";
import { StatusBadge } from "./StatusBadge";

// Exported so DocumentList's table columns reuse the exact same formatting, and so
// both are directly unit-testable without rendering a table or a modal.
export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unitIndex]}`;
}

export function formatDateShort(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateFull(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * Passive, read-only document detail view — the fix for "clicking a row does
 * nothing" from the UX audit. Deliberately carries no edit/delete controls of
 * its own; those already exist as row-level hover actions in DocumentList and
 * are not duplicated here.
 */
export function DocumentDetailModal({
  document,
  onClose,
}: {
  document: Document | null;
  onClose: () => void;
}) {
  const titleId = useId();

  return (
    <Modal open={document !== null} onClose={onClose} titleId={titleId}>
      {document && (
        <>
          <div className="flex items-start justify-between gap-4">
            <h2 id={titleId} className="text-base font-semibold break-all">
              {document.title}
            </h2>
            <StatusBadge status={document.status} failedStage={document.failed_stage} />
          </div>
          {document.status === "FAILED" && document.error_detail && (
            <p className="text-sm text-danger">{document.error_detail}</p>
          )}

          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted">Type</dt>
            <dd>{document.mime_type ?? "—"}</dd>

            <dt className="text-muted">Size</dt>
            <dd>{formatBytes(document.byte_size)}</dd>

            <dt className="text-muted">Pages</dt>
            <dd>{document.page_count ?? "—"}</dd>

            <dt className="text-muted">Language</dt>
            <dd>{document.language ?? "—"}</dd>

            <dt className="text-muted">Uploaded by</dt>
            <dd>{document.uploader_email ?? "—"}</dd>

            <dt className="text-muted">Uploaded</dt>
            <dd>{formatDateFull(document.created_at)}</dd>
          </dl>

          {document.checksum && (
            <div className="pt-1">
              <p className="text-xs text-muted mb-1">Checksum</p>
              <code className="block text-xs text-muted break-all bg-bg border border-border rounded-md px-2 py-1">
                {document.checksum}
              </code>
            </div>
          )}

          <div className="flex justify-end pt-2">
            <button
              type="button"
              onClick={onClose}
              className="text-sm border border-border rounded-md px-3 py-1.5 text-muted hover:text-text hover:bg-surface"
            >
              Close
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
