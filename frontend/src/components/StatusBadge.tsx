import type { DocumentStatus } from "../models/documents";

const LABEL: Record<DocumentStatus, string> = {
  UPLOADED: "Uploaded",
  PARSING: "Parsing",
  STRUCTURING: "Structuring",
  EMBEDDING: "Embedding",
  READY: "Ready",
  FAILED: "Failed",
};

// uitokens.md: warning = in-progress stages, success = ready, danger = failed.
const COLOR: Record<DocumentStatus, string> = {
  UPLOADED: "text-warning border-warning",
  PARSING: "text-warning border-warning",
  STRUCTURING: "text-warning border-warning",
  EMBEDDING: "text-warning border-warning",
  READY: "text-success border-success",
  FAILED: "text-danger border-danger",
};

// failed_stage is its own free-text field on the wire (not a DocumentStatus value) —
// only fall back to LABEL's friendlier names when it happens to match one exactly.
function isKnownStatus(value: string): value is DocumentStatus {
  return value in LABEL;
}

export function StatusBadge({
  status,
  failedStage,
}: {
  status: DocumentStatus;
  failedStage?: string | null;
}) {
  const failedStageLabel = failedStage
    ? isKnownStatus(failedStage)
      ? LABEL[failedStage]
      : failedStage
    : null;
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs border rounded-sm px-2 py-0.5 ${COLOR[status]}`}
    >
      {LABEL[status]}
      {status === "FAILED" && failedStageLabel ? ` (${failedStageLabel})` : null}
    </span>
  );
}
