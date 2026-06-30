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

// ── Notebooks (knowledge bases) ────────────────────────────────────────────
// Types mirror app.knowledge.schemas.NotebookOut exactly.
export type Notebook = {
  id: string;
  org_id: string;
  name: string;
  description: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

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

// ── Chat ────────────────────────────────────────────────────────────────────
// Types mirror app.chat.schemas exactly.
export type ResolvedCitation = {
  marker: number;
  document_id: string;
  chunk_id: string;
  char_start: number;
  char_end: number;
  content: string;
};

export type ChatResponse = {
  correlation_id: string;
  conversation_id: string;
  message_id: string;
  notebook_id: string;
  query: string;
  answer: string;
  citations: ResolvedCitation[];
  model: string;
};

export type ChatRequest = {
  notebook_id: string;
  query: string;
  k?: number;
};

// SSE event shapes for the /chat/stream endpoint (F4x).
export type SSETokenEvent = { type: "token"; content: string };
export type SSEDoneEvent = ChatResponse & { type: "done" };
export type SSEErrorEvent = { type: "error"; message: string };
export type SSEEvent = SSETokenEvent | SSEDoneEvent | SSEErrorEvent;

export const chatApi = {
  /**
   * Streams the answer token-by-token via fetch + ReadableStream (not EventSource —
   * POST bodies require fetch). Returns a cleanup function that aborts the stream;
   * callers must invoke it on component unmount to avoid state updates on dead trees.
   */
  streamAsk: (
    params: ChatRequest,
    callbacks: {
      onToken: (token: string) => void;
      onDone: (response: ChatResponse) => void;
      onError: (message: string) => void;
    },
  ): (() => void) => {
    const controller = new AbortController();

    void (async () => {
      const headers = new Headers({ "Content-Type": "application/json" });
      const token = getStoredAccessToken();
      if (token) headers.set("Authorization", `Bearer ${token}`);
      try {
        const res = await fetch("/chat/stream", {
          method: "POST",
          headers,
          body: JSON.stringify(params),
          signal: controller.signal,
          credentials: "include",
        });
        if (!res.ok || !res.body) {
          callbacks.onError(`Request failed (${res.status})`);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            const chunk = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            if (chunk.startsWith("data: ")) {
              try {
                const event = JSON.parse(chunk.slice(6)) as SSEEvent;
                if (event.type === "token") callbacks.onToken(event.content);
                else if (event.type === "done") callbacks.onDone(event);
                else if (event.type === "error") callbacks.onError(event.message);
              } catch {
                /* ignore malformed SSE lines */
              }
            }
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch (err) {
        if (err instanceof Error && err.name !== "AbortError") {
          callbacks.onError("Stream disconnected");
        }
      }
    })();

    return () => controller.abort();
  },
};
