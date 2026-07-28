// Mirrors app.models.evals.GoldenQuestionOut.
export type GoldenQuestion = {
  id: string;
  notebook_id: string;
  source_message_id: string | null;
  question: string;
  reference_answer: string;
  reference_contexts: string[];
  status: string;
  created_by: string | null;
  created_at: string;
};
