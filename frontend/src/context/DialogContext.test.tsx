import { describe, expect, it } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useState } from "react";
import { DialogProvider } from "./DialogContext";
import { useDialog } from "../hooks/useDialog";

function ConfirmHarness() {
  const dialog = useDialog();
  const [result, setResult] = useState("pending");
  return (
    <div>
      <p>result: {result}</p>
      <button
        onClick={() => {
          void dialog
            .confirm("Are you sure?", { confirmLabel: "Delete", danger: true })
            .then((ok) => setResult(ok ? "confirmed" : "cancelled"));
        }}
      >
        Ask
      </button>
    </div>
  );
}

function AlertHarness() {
  const dialog = useDialog();
  const [done, setDone] = useState(false);
  return (
    <div>
      <p>done: {String(done)}</p>
      <button
        onClick={() => {
          void dialog.alert("Something happened").then(() => setDone(true));
        }}
      >
        Notify
      </button>
    </div>
  );
}

describe("DialogContext / useDialog", () => {
  it("confirm() shows Cancel/Confirm and resolves true when the confirm button is clicked", async () => {
    render(
      <DialogProvider>
        <ConfirmHarness />
      </DialogProvider>,
    );
    fireEvent.click(screen.getByText("Ask"));
    await waitFor(() => expect(screen.getByText("Are you sure?")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    const confirmButton = screen.getByRole("button", { name: "Delete" });
    expect(confirmButton).toBeInTheDocument();

    fireEvent.click(confirmButton);
    await waitFor(() => expect(screen.getByText("result: confirmed")).toBeInTheDocument());
  });

  it("confirm() resolves false when Cancel is clicked", async () => {
    render(
      <DialogProvider>
        <ConfirmHarness />
      </DialogProvider>,
    );
    fireEvent.click(screen.getByText("Ask"));
    await waitFor(() => expect(screen.getByText("Are you sure?")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.getByText("result: cancelled")).toBeInTheDocument());
  });

  it("confirm() resolves false on Escape (treated as cancel)", async () => {
    render(
      <DialogProvider>
        <ConfirmHarness />
      </DialogProvider>,
    );
    fireEvent.click(screen.getByText("Ask"));
    await waitFor(() => expect(screen.getByText("Are you sure?")).toBeInTheDocument());

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(screen.getByText("result: cancelled")).toBeInTheDocument());
  });

  it("alert() shows a single OK button and resolves when clicked", async () => {
    render(
      <DialogProvider>
        <AlertHarness />
      </DialogProvider>,
    );
    fireEvent.click(screen.getByText("Notify"));
    await waitFor(() => expect(screen.getByText("Something happened")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(screen.getByText("done: true")).toBeInTheDocument());
  });
});
