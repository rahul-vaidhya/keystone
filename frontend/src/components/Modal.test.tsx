import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Modal } from "./Modal";

describe("Modal", () => {
  it("renders nothing when closed", () => {
    render(
      <Modal open={false} onClose={vi.fn()} titleId="t">
        <p>content</p>
      </Modal>,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("content")).not.toBeInTheDocument();
  });

  it("renders with role='dialog' and aria-modal='true' when open", () => {
    render(
      <Modal open={true} onClose={vi.fn()} titleId="t">
        <h2 id="t">Title</h2>
      </Modal>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "t");
  });

  it("calls onClose when Escape is pressed", () => {
    const onClose = vi.fn();
    render(
      <Modal open={true} onClose={onClose} titleId="t">
        <button>Inside</button>
      </Modal>,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the backdrop is clicked", () => {
    const onClose = vi.fn();
    render(
      <Modal open={true} onClose={onClose} titleId="t">
        <button>Inside</button>
      </Modal>,
    );
    // The dialog panel's parent is the fixed backdrop overlay — clicking it
    // (outside the panel) should dismiss, mirroring "click outside to close".
    fireEvent.click(screen.getByRole("dialog").parentElement!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not call onClose when clicking inside the panel", () => {
    const onClose = vi.fn();
    render(
      <Modal open={true} onClose={onClose} titleId="t">
        <button>Inside</button>
      </Modal>,
    );
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();
  });
});
