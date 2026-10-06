import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiFetch,
  consumeSessionNotice,
  getStoredAccessToken,
  SESSION_ENDED_NOTICE,
  SESSION_NOTICE_KEY,
  setSessionExpiredHandler,
  setStoredAccessToken,
} from "./http";

function jsonResponse(status: number, statusText: string, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch error detail extraction", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("joins a Pydantic array-of-objects 422 detail into a readable message, not [object Object]", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, "Unprocessable Entity", {
          detail: [
            {
              loc: ["body", "password"],
              msg: "String should have at least 8 characters",
              type: "string_too_short",
            },
          ],
        }),
      ),
    );

    await expect(apiFetch("/auth/signup", { method: "POST" })).rejects.toMatchObject({
      status: 422,
      message: "String should have at least 8 characters",
    });
  });

  it("joins multiple Pydantic validation errors with '; '", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, "Unprocessable Entity", {
          detail: [
            { loc: ["body", "password"], msg: "String should have at least 8 characters", type: "string_too_short" },
            { loc: ["body", "email"], msg: "value is not a valid email address", type: "value_error" },
          ],
        }),
      ),
    );

    await expect(apiFetch("/auth/signup", { method: "POST" })).rejects.toMatchObject({
      status: 422,
      message:
        "String should have at least 8 characters; value is not a valid email address",
    });
  });

  it("passes a plain string detail (e.g. 403 Forbidden) through unchanged", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(403, "Forbidden", { detail: "Not authorized" })),
    );

    await expect(apiFetch("/documents/folders/1")).rejects.toMatchObject({
      status: 403,
      message: "Not authorized",
    });
  });

  it("falls back to statusText when detail is missing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(500, "Internal Server Error", {})),
    );

    await expect(apiFetch("/documents")).rejects.toMatchObject({
      status: 500,
      message: "Internal Server Error",
    });
  });

  it("falls back to statusText when detail is malformed (neither string nor array)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(500, "Internal Server Error", { detail: { unexpected: "shape" } }),
      ),
    );

    await expect(apiFetch("/documents")).rejects.toMatchObject({
      status: 500,
      message: "Internal Server Error",
    });
  });
});

describe("apiFetch session recovery on 401 (U4)", () => {
  let resetHandler: () => void = () => {};
  const onExpired = vi.fn();

  beforeEach(() => {
    sessionStorage.clear();
    onExpired.mockReset();
    resetHandler = setSessionExpiredHandler(onExpired);
  });

  afterEach(() => {
    resetHandler();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("refreshes once and replays the request with the new token", async () => {
    setStoredAccessToken("old-token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, "Unauthorized", { detail: "expired" }))
      .mockResolvedValueOnce(jsonResponse(200, "OK", { access_token: "new-token" }))
      .mockResolvedValueOnce(jsonResponse(200, "OK", [{ id: "d1" }]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/documents")).resolves.toEqual([{ id: "d1" }]);

    expect(fetchMock.mock.calls[1][0]).toBe("/auth/refresh");
    const replayHeaders = fetchMock.mock.calls[2][1].headers as Headers;
    expect(replayHeaders.get("Authorization")).toBe("Bearer new-token");
    expect(getStoredAccessToken()).toBe("new-token");
    expect(onExpired).not.toHaveBeenCalled();
  });

  it("clears the session and fires the expired handler when refresh fails", async () => {
    setStoredAccessToken("dead-token");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(401, "Unauthorized", { detail: "Account deactivated" }))
        .mockResolvedValueOnce(jsonResponse(401, "Unauthorized", { detail: "Invalid refresh token" })),
    );

    await expect(apiFetch("/documents")).rejects.toMatchObject({ status: 401 });
    expect(getStoredAccessToken()).toBeNull();
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("never treats a login 401 (wrong password) as an ended session", async () => {
    setStoredAccessToken("some-token");
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(401, "Unauthorized", { detail: "Invalid email or password" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/auth/login", { method: "POST" })).rejects.toMatchObject({
      status: 401,
      message: "Invalid email or password",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1); // no refresh attempt
    expect(onExpired).not.toHaveBeenCalled();
    expect(getStoredAccessToken()).toBe("some-token");
  });

  it("does not attempt recovery when no token was sent", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, "Unauthorized", {}));
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiFetch("/documents")).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onExpired).not.toHaveBeenCalled();
  });

  it("the default handler leaves a notice for the login page", () => {
    resetHandler();
    sessionStorage.setItem(SESSION_NOTICE_KEY, SESSION_ENDED_NOTICE);
    expect(consumeSessionNotice()).toBe(SESSION_ENDED_NOTICE);
    expect(consumeSessionNotice()).toBeNull();
  });
});
