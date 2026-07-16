import { ApiError } from "../types/auth";

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

// FastAPI's own manually-raised HTTPException(detail="...") calls (e.g. Forbidden,
// NotFoundError-style exceptions mapped by app/utils/http.py) put a plain string in
// `detail`. Pydantic validation errors (422) instead put an ARRAY of
// `{loc, msg, type}` objects in `detail` — String()-ing that array produces
// "[object Object]" rather than a readable message, so it needs its own branch.
function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body !== "object" || !body || !("detail" in body)) return fallback;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : null,
      )
      .filter((msg): msg is string => msg !== null);
    if (messages.length > 0) return messages.join("; ");
  }
  return fallback;
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
    const detail = extractErrorDetail(body, res.statusText);
    throw new ApiError(res.status, detail, body);
  }
  return body as T;
}
