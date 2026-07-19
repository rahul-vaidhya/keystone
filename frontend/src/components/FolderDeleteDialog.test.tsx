import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FolderDeleteDialog } from "./FolderDeleteDialog";

describe("FolderDeleteDialog", () => {
  it("disables the cascade button until the typed input exactly matches the folder name", () => {
    render(
      <FolderDeleteDialog open folderName="HR" onClose={vi.fn()} onChoose={vi.fn()} />,
    );
    const cascadeButton = screen.getByRole("button", { name: "Delete everything inside" });
    expect(cascadeButton).toBeDisabled();

    const input = screen.getByLabelText('Type "HR" to confirm');
    fireEvent.change(input, { target: { value: "H" } });
    expect(cascadeButton).toBeDisabled();

    fireEvent.change(input, { target: { value: "HR" } });
    expect(cascadeButton).toBeEnabled();
  });

  it("calls onChoose('cascade') once enabled", () => {
    const onChoose = vi.fn();
    render(
      <FolderDeleteDialog open folderName="HR" onClose={vi.fn()} onChoose={onChoose} />,
    );
    fireEvent.change(screen.getByLabelText('Type "HR" to confirm'), {
      target: { value: "HR" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Delete everything inside" }));

    expect(onChoose).toHaveBeenCalledWith("cascade");
  });

  it("calls onChoose('reflow') immediately, with no typed input needed", () => {
    const onChoose = vi.fn();
    render(
      <FolderDeleteDialog open folderName="HR" onClose={vi.fn()} onChoose={onChoose} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Move contents up a level/ }));

    expect(onChoose).toHaveBeenCalledWith("reflow");
  });

  it("calls onClose when Cancel is clicked", () => {
    const onClose = vi.fn();
    render(
      <FolderDeleteDialog open folderName="HR" onClose={onClose} onChoose={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when closed", () => {
    render(
      <FolderDeleteDialog open={false} folderName="HR" onClose={vi.fn()} onChoose={vi.fn()} />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
