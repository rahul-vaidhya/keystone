import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders an animated progress dot for a non-terminal status (PARSING)", () => {
    const { container } = render(<StatusBadge status="PARSING" />);
    const dot = container.querySelector(".animate-pulse");
    expect(dot).not.toBeNull();
  });

  it.each(["UPLOADED", "STRUCTURING", "EMBEDDING"] as const)(
    "renders an animated progress dot for non-terminal status %s",
    (status) => {
      const { container } = render(<StatusBadge status={status} />);
      expect(container.querySelector(".animate-pulse")).not.toBeNull();
    },
  );

  it("does NOT render an animated dot for a terminal READY status", () => {
    const { container } = render(<StatusBadge status="READY" />);
    expect(container.querySelector(".animate-pulse")).toBeNull();
  });

  it("does NOT render an animated dot for a terminal FAILED status", () => {
    const { container } = render(<StatusBadge status="FAILED" failedStage="PARSING" />);
    expect(container.querySelector(".animate-pulse")).toBeNull();
  });

  it("still renders the correct label text alongside the dot", () => {
    const { getByText } = render(<StatusBadge status="EMBEDDING" />);
    expect(getByText("Embedding")).toBeTruthy();
  });
});
