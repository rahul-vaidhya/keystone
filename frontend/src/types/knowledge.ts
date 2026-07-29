import type { ResolvedCitation } from "./chat";

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
// types/chat.ts's ResolvedCitation now carries the identical additive shape
// (citation_type/section_id/heading), so this is a plain alias rather than a
// hand-duplicated type — one shape, two call sites.
export type NotebookOverviewCitation = ResolvedCitation;

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
