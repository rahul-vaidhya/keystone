export type Folder = {
  id: string;
  org_id: string;
  parent_id: string | null;
  name: string;
  path: string;
  tag_ids: string[];
  created_at: string;
};

export type Tag = {
  id: string;
  org_id: string;
  name: string;
  created_at: string;
};

// Mirrors app.models.documents.DocumentStatus (backend's single source of truth) — the
// enum's wire value IS the upper-case name (StrEnum member = "UPLOADED" etc.), not lower-case.
export type DocumentStatus =
  | "UPLOADED"
  | "PARSING"
  | "STRUCTURING"
  | "EMBEDDING"
  | "READY"
  | "FAILED";

export type Document = {
  id: string;
  org_id: string;
  folder_id: string | null;
  title: string;
  storage_key: string | null;
  mime_type: string | null;
  byte_size: number | null;
  checksum: string | null;
  page_count: number | null;
  language: string | null;
  status: DocumentStatus;
  failed_stage: string | null;
  error_detail: string | null;
  // Resolved server-side (never a raw uploader id on the wire — see backend
  // DocumentOut.uploader_email). null for documents uploaded before this field
  // existed, or when no uploader was recorded.
  uploader_email: string | null;
  created_at: string;
};

export type FolderDeleteMode = "block" | "cascade" | "reflow";
