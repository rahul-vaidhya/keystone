import { apiFetch } from "./http";
import type { RetrievalSearchRequest, RetrievalSearchResponse } from "../types/retrieval";

export const retrievalApi = {
  search: (req: RetrievalSearchRequest) =>
    apiFetch<RetrievalSearchResponse>("/retrieval/search", {
      method: "POST",
      body: JSON.stringify(req),
    }),
};
