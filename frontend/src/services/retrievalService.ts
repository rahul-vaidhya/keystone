import { apiFetch } from "./http";
import type {
  RetrievalSearchRequest,
  RetrievalSearchResponse,
  SparseSearchRequest,
  SparseSearchResponse,
} from "../types/retrieval";

export const retrievalApi = {
  search: (req: RetrievalSearchRequest) =>
    apiFetch<RetrievalSearchResponse>("/retrieval/search", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  sparseSearch: (req: SparseSearchRequest) =>
    apiFetch<SparseSearchResponse>("/retrieval/sparse-search", {
      method: "POST",
      body: JSON.stringify(req),
    }),
};
