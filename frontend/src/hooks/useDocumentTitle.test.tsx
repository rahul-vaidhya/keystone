import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useDocumentTitle } from "./useDocumentTitle";

describe("useDocumentTitle", () => {
  it("suffixes the app name and restores it on unmount", () => {
    const { rerender, unmount } = renderHook(({ t }) => useDocumentTitle(t), {
      initialProps: { t: "Repository" as string | undefined },
    });
    expect(document.title).toBe("Repository · Keystone");
    rerender({ t: undefined });
    expect(document.title).toBe("Keystone");
    rerender({ t: "IR Demo Notebook" });
    expect(document.title).toBe("IR Demo Notebook · Keystone");
    unmount();
    expect(document.title).toBe("Keystone");
  });
});
