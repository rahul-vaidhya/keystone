import { apiFetch } from "./http";
import type { Document, Folder, FolderDeleteMode, Tag } from "../types/documents";

export const documentsApi = {
  listFolders: () => apiFetch<Folder[]>("/documents/folders"),

  createFolder: (name: string, parentId: string | null) =>
    apiFetch<Folder>("/documents/folders", {
      method: "POST",
      body: JSON.stringify({ name, parent_id: parentId }),
    }),

  renameFolder: (folderId: string, name: string) =>
    apiFetch<Folder>(`/documents/folders/${folderId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),

  moveFolder: (folderId: string, parentId: string | null) =>
    apiFetch<Folder>(`/documents/folders/${folderId}/move`, {
      method: "POST",
      body: JSON.stringify({ parent_id: parentId }),
    }),

  deleteFolder: (folderId: string, mode: FolderDeleteMode = "block") =>
    apiFetch<void>(`/documents/folders/${folderId}?mode=${mode}`, { method: "DELETE" }),

  setFolderRestriction: (folderId: string, restricted: boolean) =>
    apiFetch<Folder>(`/documents/folders/${folderId}/restriction`, {
      method: "PATCH",
      body: JSON.stringify({ restricted }),
    }),

  listTags: () => apiFetch<Tag[]>("/documents/tags"),

  createTag: (name: string) =>
    apiFetch<Tag>("/documents/tags", { method: "POST", body: JSON.stringify({ name }) }),

  deleteTag: (tagId: string) => apiFetch<void>(`/documents/tags/${tagId}`, { method: "DELETE" }),

  tagDocument: (documentId: string, tagId: string) =>
    apiFetch<void>(`/documents/${documentId}/tags/${tagId}`, { method: "POST" }),

  untagDocument: (documentId: string, tagId: string) =>
    apiFetch<void>(`/documents/${documentId}/tags/${tagId}`, { method: "DELETE" }),

  listDocuments: (params: { folderId?: string | null; tagId?: string | null } = {}) => {
    const qs = new URLSearchParams();
    if (params.folderId) qs.set("folder_id", params.folderId);
    if (params.tagId) qs.set("tag_id", params.tagId);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return apiFetch<Document[]>(`/documents${suffix}`);
  },

  uploadDocument: (file: File, folderId: string | null) => {
    const form = new FormData();
    form.append("file", file);
    if (folderId) form.append("folder_id", folderId);
    return apiFetch<Document>("/documents/upload", { method: "POST", body: form });
  },

  deleteDocument: (documentId: string) =>
    apiFetch<void>(`/documents/${documentId}`, { method: "DELETE" }),
};
