import { apiFetch } from "../lib/api";
import type { Document } from "../models/documents";
import type { Notebook } from "../models/knowledge";

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
};
