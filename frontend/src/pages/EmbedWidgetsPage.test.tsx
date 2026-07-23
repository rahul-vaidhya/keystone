import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { embedApi } from "../services/embedService";
import { notebooksApi } from "../services/notebooksService";
import type { Widget } from "../types/embed";
import type { Notebook } from "../types/knowledge";
import { DialogProvider } from "../context/DialogContext";
import { EmbedWidgetsPage } from "./EmbedWidgetsPage";

vi.mock("../services/embedService", () => ({
  embedApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
  },
}));

vi.mock("../services/notebooksService", () => ({
  notebooksApi: {
    list: vi.fn(),
  },
}));

function makeNotebook(overrides: Partial<Notebook> = {}): Notebook {
  return {
    id: "nb-1",
    org_id: "org-1",
    name: "Chemistry",
    description: null,
    created_by: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeWidget(overrides: Partial<Widget> = {}): Widget {
  return {
    id: "w-1",
    name: "Support bot",
    knowledge_base_id: "nb-1",
    public_id: "pub-abc123",
    allowed_origins: ["https://example.com"],
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    embed_snippet:
      '<script src="http://localhost:5173/widget.js" data-org="org-1" data-widget-id="pub-abc123" async></script>',
    iframe_url: "http://localhost:5173/embed?org=org-1&widget=pub-abc123",
    ...overrides,
  };
}

function renderWithClient() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <DialogProvider>
        <EmbedWidgetsPage />
      </DialogProvider>
    </QueryClientProvider>,
  );
}

describe("EmbedWidgetsPage", () => {
  beforeEach(() => {
    vi.mocked(embedApi.list).mockReset();
    vi.mocked(embedApi.create).mockReset();
    vi.mocked(embedApi.update).mockReset();
    vi.mocked(embedApi.remove).mockReset();
    vi.mocked(notebooksApi.list).mockReset().mockResolvedValue([makeNotebook()]);
  });

  it("shows an empty state when there are no widgets", async () => {
    vi.mocked(embedApi.list).mockResolvedValue([]);

    renderWithClient();

    await waitFor(() => expect(screen.getByText(/No widgets yet/)).toBeInTheDocument());
  });

  it("renders the widget list from embedApi.list, including notebook name and origins", async () => {
    vi.mocked(embedApi.list).mockResolvedValue([makeWidget()]);

    renderWithClient();

    await waitFor(() => expect(screen.getByText("Support bot")).toBeInTheDocument());
    // "Chemistry" also appears as an <option> in the create form's notebook select —
    // scope to the widget card's notebook-name <p> to avoid a multiple-match error.
    await waitFor(() =>
      expect(screen.getByText("Chemistry", { selector: "p" })).toBeInTheDocument(),
    );
    expect(screen.getByText("https://example.com")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("shows the 'Any site' warning when allowed_origins is empty", async () => {
    vi.mocked(embedApi.list).mockResolvedValue([makeWidget({ allowed_origins: [] })]);

    renderWithClient();

    await waitFor(() => expect(screen.getByText("Any site ⚠")).toBeInTheDocument());
  });

  it("creates a widget with the parsed origins list", async () => {
    vi.mocked(embedApi.list).mockResolvedValue([]);
    vi.mocked(embedApi.create).mockResolvedValue(makeWidget());

    renderWithClient();
    await waitFor(() => expect(notebooksApi.list).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole("option", { name: "Chemistry" })).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Support bot" } });
    fireEvent.change(screen.getByLabelText("Notebook"), { target: { value: "nb-1" } });
    fireEvent.change(screen.getByLabelText("Allowed origins (one per line)"), {
      target: { value: "https://example.com\n\n  https://foo.com  \n" },
    });
    fireEvent.click(screen.getByText("Create widget"));

    await waitFor(() =>
      expect(embedApi.create).toHaveBeenCalledWith({
        knowledge_base_id: "nb-1",
        name: "Support bot",
        allowed_origins: ["https://example.com", "https://foo.com"],
      }),
    );
  });

  it("revokes a widget after the dialog confirm is accepted", async () => {
    vi.mocked(embedApi.list).mockResolvedValue([makeWidget({ is_active: true })]);
    vi.mocked(embedApi.update).mockResolvedValue(makeWidget({ is_active: false }));

    renderWithClient();
    await waitFor(() => expect(screen.getByText("Support bot")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Revoke"));

    // Real dialog UI, not window.confirm — the danger-styled confirm button.
    await waitFor(() => expect(screen.getByText("Confirm")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Revoke", { selector: "button.bg-danger" }));

    await waitFor(() =>
      expect(embedApi.update).toHaveBeenCalledWith("w-1", { is_active: false }),
    );
  });

  it("deletes a widget after the dialog confirm is accepted", async () => {
    vi.mocked(embedApi.list).mockResolvedValue([makeWidget()]);

    renderWithClient();
    await waitFor(() => expect(screen.getByText("Support bot")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete Support bot"));

    await waitFor(() => expect(screen.getByText(/This cannot be undone/)).toBeInTheDocument());
    fireEvent.click(screen.getByText("Delete", { selector: "button.bg-danger" }));

    await waitFor(() => expect(embedApi.remove).toHaveBeenCalledWith("w-1"));
  });

  it("shows and copies the embed snippet and iframe URL", async () => {
    vi.mocked(embedApi.list).mockResolvedValue([makeWidget()]);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    });

    renderWithClient();
    await waitFor(() => expect(screen.getByText("Support bot")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Get embed code"));

    await waitFor(() => expect(screen.getByText("Script snippet")).toBeInTheDocument());
    expect(screen.getByText("Iframe URL")).toBeInTheDocument();

    const copyButtons = screen.getAllByText("Copy");
    fireEvent.click(copyButtons[0]);

    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining("widget.js"),
      ),
    );
    await waitFor(() => expect(screen.getAllByText("Copied").length).toBeGreaterThan(0));
  });
});
