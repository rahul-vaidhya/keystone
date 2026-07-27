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
