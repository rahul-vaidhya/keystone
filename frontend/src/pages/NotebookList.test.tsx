import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { Notebook } from "../types/knowledge";
import { notebooksApi } from "../services/notebooksService";
import { useAuth } from "../hooks/useAuth";
import { DialogProvider } from "../context/DialogContext";
import { NotebookList } from "./NotebookList";

vi.mock("../services/notebooksService", async () => {
  const actual = await vi.importActual<typeof import("../services/notebooksService")>(
    "../services/notebooksService",
  );
  return {
    ...actual,
    notebooksApi: {
      list: vi.fn(),
      create: vi.fn(),
      delete: vi.fn(),
      update: vi.fn(),
    },
  };
});

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function mockUser(id = "u-1") {
  vi.mocked(useAuth).mockReturnValue({
    user: {
      id,
      org_id: "org-1",
      email: "u@test.com",
      name: null,
      role: "member",
      is_active: true,
      created_at: "2026-01-01T00:00:00Z",
    },
    loading: false,
    login: vi.fn(),
    signup: vi.fn(),
    acceptInvite: vi.fn(),
    logout: vi.fn(),
  });
}

// useNavigate is real but we don't assert on navigation in unit tests — wrapping with
// MemoryRouter is enough to satisfy the hook, and RTL/jsdom doesn't have a real browser.
// created_by defaults to the mocked user's own id ("u-1") so existing owner-flow tests
// (delete button visible, etc.) keep their prior behavior unchanged.
function makeNotebook(overrides: Partial<Notebook> = {}): Notebook {
  return {
    id: "nb-1",
    org_id: "org-1",
    name: "Chemistry Notes",
    description: null,
    created_by: "u-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderWithAll(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DialogProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </DialogProvider>
    </QueryClientProvider>,
  );
}

describe("NotebookList", () => {
  beforeEach(() => {
    vi.mocked(notebooksApi.list).mockReset();
    vi.mocked(notebooksApi.create).mockReset();
    vi.mocked(notebooksApi.delete).mockReset();
    vi.mocked(notebooksApi.update).mockReset();
    mockUser();
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

  it("the delete button reveals on keyboard focus, not just hover (WCAG 2.1.1)", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook()]);

    renderWithAll(<NotebookList />);
    await waitFor(() => expect(screen.getByText("Chemistry Notes")).toBeInTheDocument());

    const deleteButton = screen.getByLabelText("Delete Chemistry Notes");
    expect(deleteButton.className).toMatch(/group-focus-within:opacity-100/);
    expect(deleteButton.className).toMatch(/focus-visible:opacity-100/);
  });

  it("deletes a notebook via notebooksApi.delete after confirming in the dialog and invalidates the list", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook()]);
    vi.mocked(notebooksApi.delete).mockResolvedValue(undefined);

    renderWithAll(<NotebookList />);
    await waitFor(() => expect(screen.getByText("Chemistry Notes")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete Chemistry Notes"));
    await waitFor(() =>
      expect(screen.getByText('Delete notebook "Chemistry Notes"?')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(notebooksApi.delete).toHaveBeenCalledWith("nb-1"));
    await waitFor(() => expect(notebooksApi.list).toHaveBeenCalledTimes(2));
  });

  it("does not delete a notebook when the confirm dialog is cancelled", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook()]);

    renderWithAll(<NotebookList />);
    await waitFor(() => expect(screen.getByText("Chemistry Notes")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete Chemistry Notes"));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(notebooksApi.delete).not.toHaveBeenCalled();
  });

  it("shows a 'Shared' badge and hides delete for a notebook the user doesn't own", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([
      makeNotebook({ name: "Someone else's", created_by: "u-2" }),
    ]);

    renderWithAll(<NotebookList />);
    await waitFor(() => expect(screen.getByText("Someone else's")).toBeInTheDocument());

    expect(screen.getByText("Shared")).toBeInTheDocument();
    expect(screen.queryByLabelText("Delete Someone else's")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Rename Someone else's")).not.toBeInTheDocument();
  });

  it("renames an owned notebook from the list without navigating into it", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook({ name: "Physics" })]);
    vi.mocked(notebooksApi.update).mockResolvedValue(makeNotebook({ name: "Physics II" }));

    renderWithAll(<NotebookList />);
    await waitFor(() => expect(screen.getByText("Physics")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Rename Physics"));
    fireEvent.change(screen.getByLabelText("Notebook name"), { target: { value: "Physics II" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(notebooksApi.update).toHaveBeenCalledWith(expect.any(String), { name: "Physics II" }),
    );
  });
});
