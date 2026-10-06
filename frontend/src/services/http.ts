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

// ---- Session expiry (U4) ----
// When a request made WITH an access token comes back 401 (deactivated account,
// password changed elsewhere, expired token), try the refresh-cookie flow once and
// replay the request. If refresh fails, the session is genuinely over: clear the
// token and hand off to the session-expired handler (default: hard redirect to the
// login page, which shows SESSION_ENDED_NOTICE).
//
// Credential endpoints are excluded: their 401s mean "wrong password"/"bad invite"
// and must surface as inline form errors, never as a logout.
const NO_SESSION_RECOVERY_PATHS = [
  "/auth/login",
  "/auth/signup",
  "/auth/refresh",
  "/auth/logout",
  "/auth/accept-invite",
  "/auth/me/password",
];

export const SESSION_NOTICE_KEY = "veratas_session_notice";
export const SESSION_ENDED_NOTICE = "Your session has ended — please sign in again.";

function defaultSessionExpiredHandler(): void {
  try {
    sessionStorage.setItem(SESSION_NOTICE_KEY, SESSION_ENDED_NOTICE);
  } catch {
    // storage unavailable — the redirect alone still recovers the UI
  }
  if (window.location.pathname !== "/login") window.location.assign("/login");
}

let sessionExpiredHandler: () => void = defaultSessionExpiredHandler;

/** Override what happens when the session can't be recovered (tests). Returns a reset fn. */
export function setSessionExpiredHandler(handler: () => void): () => void {
  sessionExpiredHandler = handler;
  return () => {
    sessionExpiredHandler = defaultSessionExpiredHandler;
  };
}

/** Read the "session ended" notice for the login page (pure — safe in render). */
export function peekSessionNotice(): string | null {
  try {
    return sessionStorage.getItem(SESSION_NOTICE_KEY);
  } catch {
    return null;
  }
}

export function clearSessionNotice(): void {
  try {
    sessionStorage.removeItem(SESSION_NOTICE_KEY);
  } catch {
    // ignore
  }
}

/** Read-and-clear the "session ended" notice. */
export function consumeSessionNotice(): string | null {
  const notice = peekSessionNotice();
  if (notice) clearSessionNotice();
  return notice;
}

function isRecoverablePath(path: string): boolean {
  return !NO_SESSION_RECOVERY_PATHS.some((p) => path === p || path.startsWith(`${p}?`));
}

// Single-flight: N parallel queries hitting 401 at once share one refresh call.
let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch("/auth/refresh", { method: "POST", credentials: "include" });
        if (!res.ok) return null;
        const body = (await parseJson(res)) as { access_token?: string } | null;
        return body?.access_token ?? null;
      } catch {
        return null;
      }
    })().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function send(path: string, init: RequestInit, access: string | null): Promise<Response> {
  const headers = new Headers(init.headers);
  // FormData bodies (multipart upload) must NOT get a Content-Type set here — the
  // browser sets it itself, including the multipart boundary; forcing application/json
  // would silently break the upload's multipart parsing on the backend.
  if (!headers.has("Content-Type") && init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (access) headers.set("Authorization", `Bearer ${access}`);
  return fetch(path, { ...init, headers, credentials: "include" });
}

/** Like apiFetch, but also returns the HTTP status (e.g. 201 created vs 200 existing). */
export async function apiFetchWithStatus<T>(
  path: string,
  init: RequestInit = {},
  token?: string | null,
): Promise<{ status: number; data: T }> {
  const access = token ?? getStoredAccessToken();
  let res = await send(path, init, access);

  if (res.status === 401 && access && token == null && isRecoverablePath(path)) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      setStoredAccessToken(refreshed);
      res = await send(path, init, refreshed);
    } else {
      setStoredAccessToken(null);
      sessionExpiredHandler();
    }
  }

  const body = await parseJson(res);
  if (!res.ok) {
    const detail = extractErrorDetail(body, res.statusText);
    throw new ApiError(res.status, detail, body);
  }
  return { status: res.status, data: body as T };
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  token?: string | null,
): Promise<T> {
  return (await apiFetchWithStatus<T>(path, init, token)).data;
}
