// Mirrors app.models.knowledge.NotebookOut exactly.
export type Notebook = {
  id: string;
  org_id: string;
  name: string;
  description: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

// Mirrors app.models.knowledge.NotebookShareOut exactly.
export type NotebookShare = {
  user_id: string;
  email: string;
  created_at: string;
};

// Mirrors app.models.chat.ResolvedCitation's ADDITIVE shape as used on the section
// (map-reduce) path only (citation_type is always "section" here — the Overview
// artifact is always synthesized from section summaries, never individual chunks).
// Deliberately a separate type from types/chat.ts's ResolvedCitation (which hasn't
// been updated for the additive citation_type/section_id/heading fields yet — that's
// the P1 broad-query chat-UI indicator, a different, not-yet-built feature) rather than
// widening that type as a side effect of this one.
export type NotebookOverviewCitation = {
  marker: number;
  document_id: string;
  chunk_id: string | null;
  char_start: number | null;
  char_end: number | null;
  content: string;
  page_start: number | null;
  page_end: number | null;
  citation_type: "chunk" | "section";
  section_id: string | null;
  heading: string | null;
};

// Mirrors app.models.knowledge.NotebookOverviewOut exactly.
export type NotebookOverview = {
  id: string;
  notebook_id: string;
  content: string;
  citations: NotebookOverviewCitation[];
  generated_at: string;
  generated_by: string | null;
  source_document_count: number;
  stale: boolean;
};
