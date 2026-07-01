import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { Notebook } from "../../models/knowledge";
import { notebooksApi } from "../../controllers/notebooksController";
import { NotebookList } from "./NotebookList";

vi.mock("../../controllers/notebooksController", async () => {
  const actual = await vi.importActual<typeof import("../../controllers/notebooksController")>(
    "../../controllers/notebooksController",
  );
  return {
    ...actual,
    notebooksApi: {
      list: vi.fn(),
      create: vi.fn(),
      delete: vi.fn(),
    },
  };
});

// useNavigate is real but we don't assert on navigation in unit tests — wrapping with
// MemoryRouter is enough to satisfy the hook, and RTL/jsdom doesn't have a real browser.
function makeNotebook(overrides: Partial<Notebook> = {}): Notebook {
  return {
    id: "nb-1",
    org_id: "org-1",
    name: "Chemistry Notes",
    description: null,
    created_by: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderWithAll(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NotebookList", () => {
  beforeEach(() => {
    vi.mocked(notebooksApi.list).mockReset();
    vi.mocked(notebooksApi.create).mockReset();
    vi.mocked(notebooksApi.delete).mockReset();
  });

  it("renders a list of notebooks returned by notebooksApi.list", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([
      makeNotebook({ id: "nb-1", name: "Chemistry" }),
      makeNotebook({ id: "nb-2", name: "Finance", description: "Q4 reports" }),
    ]);

    renderWithAll(<NotebookList />);

    await waitFor(() => expect(screen.getByText("Chemistry")).toBeInTheDocument());
    expect(screen.getByText("Finance")).toBeInTheDocument();
    expect(screen.getByText("Q4 reports")).toBeInTheDocument();
  });

  it("shows an empty-state prompt when there are no notebooks", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([]);
    renderWithAll(<NotebookList />);
    await waitFor(() =>
      expect(screen.getByText(/No notebooks yet/)).toBeInTheDocument(),
    );
  });

  it("creates a notebook via notebooksApi.create and invalidates the list", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([]);
    vi.mocked(notebooksApi.create).mockResolvedValue(makeNotebook({ name: "New NB" }));

    renderWithAll(<NotebookList />);
    await waitFor(() => expect(notebooksApi.list).toHaveBeenCalled());

    fireEvent.click(screen.getByText("New notebook"));
    const input = screen.getByPlaceholderText("Notebook name");
    fireEvent.change(input, { target: { value: "New NB" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(notebooksApi.create).toHaveBeenCalledWith("New NB", null),
    );
    await waitFor(() => expect(notebooksApi.list).toHaveBeenCalledTimes(2));
  });

  it("deletes a notebook via notebooksApi.delete after confirm and invalidates the list", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook()]);
    vi.mocked(notebooksApi.delete).mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithAll(<NotebookList />);
    await waitFor(() => expect(screen.getByText("Chemistry Notes")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete Chemistry Notes"));

    await waitFor(() => expect(notebooksApi.delete).toHaveBeenCalledWith("nb-1"));
    await waitFor(() => expect(notebooksApi.list).toHaveBeenCalledTimes(2));
  });
});
