import { apiFetch } from "./http";
import type { Document } from "../types/documents";
import type { Notebook, NotebookOverview, NotebookShare } from "../types/knowledge";

export const notebooksApi = {
  list: () => apiFetch<Notebook[]>("/notebooks"),

  get: (id: string) => apiFetch<Notebook>(`/notebooks/${id}`),

  create: (name: string, description?: string | null) =>
    apiFetch<Notebook>("/notebooks", {
      method: "POST",
      body: JSON.stringify({ name, description: description ?? null }),
    }),

  update: (id: string, patch: { name?: string; description?: string | null }) =>
    apiFetch<Notebook>(`/notebooks/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  delete: (id: string) => apiFetch<void>(`/notebooks/${id}`, { method: "DELETE" }),

  listDocuments: (notebookId: string) =>
    apiFetch<Document[]>(`/notebooks/${notebookId}/documents`),

  attachDocument: (notebookId: string, documentId: string) =>
    apiFetch<void>(`/notebooks/${notebookId}/documents/${documentId}`, { method: "POST" }),

  detachDocument: (notebookId: string, documentId: string) =>
    apiFetch<void>(`/notebooks/${notebookId}/documents/${documentId}`, { method: "DELETE" }),

  listShares: (notebookId: string) =>
    apiFetch<NotebookShare[]>(`/notebooks/${notebookId}/shares`),

  share: (notebookId: string, userId: string) =>
    apiFetch<void>(`/notebooks/${notebookId}/shares`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),

  unshare: (notebookId: string, userId: string) =>
    apiFetch<void>(`/notebooks/${notebookId}/shares/${userId}`, { method: "DELETE" }),

  // P1 Notebook Overview: on-demand, cached "gist of everything" artifact.
  getOverview: (notebookId: string) =>
    apiFetch<NotebookOverview>(`/notebooks/${notebookId}/overview`),

  generateOverview: (notebookId: string) =>
    apiFetch<NotebookOverview>(`/notebooks/${notebookId}/overview`, { method: "POST" }),
};
