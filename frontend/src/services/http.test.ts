import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./http";

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
