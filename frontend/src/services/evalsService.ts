import { apiFetch } from "./http";
import type { GoldenQuestion } from "../types/evals";

// Golden-eval curation (admin-only; the backend 403s for a non-admin). Grows the
// golden-question set from a real graded /chat/ask answer via the admin Debug panel's
// "Add to golden set" button — everything but message_id is derived server-side.
export const evalsApi = {
  addGoldenQuestion: (messageId: string) =>
    apiFetch<GoldenQuestion>("/evals/golden-questions", {
      method: "POST",
      body: JSON.stringify({ message_id: messageId }),
    }),

  listGoldenQuestions: () => apiFetch<GoldenQuestion[]>("/evals/golden-questions"),
};
