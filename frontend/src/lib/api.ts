export type User = {
  id: string;
  org_id: string;
  email: string;
  role: "owner" | "admin" | "member";
  created_at: string;
};

export type TokenResponse = {
  access_token: string;
  token_type: string;
};

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

const ACCESS_KEY = "veratas_access_token";

export function getStoredAccessToken(): string | null {
  return sessionStorage.getItem(ACCESS_KEY);
}

export function setStoredAccessToken(token: string | null): void {
  if (token) sessionStorage.setItem(ACCESS_KEY, token);
  else sessionStorage.removeItem(ACCESS_KEY);
}

async function parseJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  token?: string | null,
): Promise<T> {
  const headers = new Headers(init.headers);
  // FormData bodies (multipart upload) must NOT get a Content-Type set here — the
  // browser sets it itself, including the multipart boundary; forcing application/json
  // would silently break the upload's multipart parsing on the backend.
  if (!headers.has("Content-Type") && init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const access = token ?? getStoredAccessToken();
  if (access) headers.set("Authorization", `Bearer ${access}`);

  const res = await fetch(path, {
    ...init,
    headers,
    credentials: "include",
  });

  const body = await parseJson(res);
  if (!res.ok) {
    const detail =
      typeof body === "object" && body && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : res.statusText;
    throw new ApiError(res.status, detail, body);
  }
  return body as T;
}

export const authApi = {
  signup: (email: string, password: string, org_name: string) =>
    apiFetch<TokenResponse>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, org_name }),
    }),

  login: (email: string, password: string, org_id?: string) =>
    apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password, org_id }),
    }),

  refresh: () =>
    apiFetch<TokenResponse>("/auth/refresh", { method: "POST" }),

  logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),

  me: () => apiFetch<User>("/auth/me"),

  listUsers: () => apiFetch<User[]>("/auth/users"),

  changeRole: (userId: string, role: "admin" | "member") =>
    apiFetch<User>(`/auth/users/${userId}/role`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
};

export type Folder = {
  id: string;
  org_id: string;
  parent_id: string | null;
  name: string;
  path: string;
  created_at: string;
};

export type Tag = {
  id: string;
  org_id: string;
  name: string;
  created_at: string;
};

// Mirrors app.documents.status.DocumentStatus (backend's single source of truth) — the
// enum's wire value IS the upper-case name (StrEnum member = "UPLOADED" etc.), not lower-case.
export type DocumentStatus =
  | "UPLOADED"
  | "PARSING"
  | "STRUCTURING"
  | "EMBEDDING"
  | "READY"
  | "FAILED";

export type Document = {
  id: string;
  org_id: string;
  folder_id: string | null;
  title: string;
  storage_key: string | null;
  mime_type: string | null;
  byte_size: number | null;
  checksum: string | null;
  page_count: number | null;
  language: string | null;
  status: DocumentStatus;
  failed_stage: string | null;
  error_detail: string | null;
  created_at: string;
};

export type FolderDeleteMode = "block" | "cascade" | "reflow";

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
};
