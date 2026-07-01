// Mirrors app.schemas.knowledge.NotebookOut exactly.
export type Notebook = {
  id: string;
  org_id: string;
  name: string;
  description: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};
