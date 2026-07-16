import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ApiError } from "../types/auth";
import { AcceptInvitePage } from "./AcceptInvitePage";

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function mockAuth(acceptInvite = vi.fn()) {
  vi.mocked(useAuth).mockReturnValue({
    user: null,
    loading: false,
    login: vi.fn(),
    signup: vi.fn(),
    acceptInvite,
    logout: vi.fn(),
  });
  return acceptInvite;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AcceptInvitePage />
    </MemoryRouter>,
  );
}

describe("AcceptInvitePage", () => {
  beforeEach(() => {
    mockAuth();
  });

  it("shows an invalid-link message when org or token is missing from the URL", () => {
    renderAt("/accept-invite");

    expect(
      screen.getByText("This invite link is invalid or incomplete."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/Password/)).not.toBeInTheDocument();
  });

  it("shows an invalid-link message when only one of org/token is present", () => {
    renderAt("/accept-invite?org=org-1");

    expect(
      screen.getByText("This invite link is invalid or incomplete."),
    ).toBeInTheDocument();
  });

  it("calls acceptInvite with the org, token, and entered password on submit", async () => {
    const acceptInvite = mockAuth();
    renderAt("/accept-invite?org=org-1&token=raw-token-abc");

    fireEvent.change(screen.getByLabelText(/Password/), {
      target: { value: "brandnew123" },
    });
    fireEvent.click(screen.getByText("Join workspace"));

    await waitFor(() =>
      expect(acceptInvite).toHaveBeenCalledWith("org-1", "raw-token-abc", "brandnew123"),
    );
  });

  it("renders the API error message inline on failure", async () => {
    const acceptInvite = vi
      .fn()
      .mockRejectedValue(new ApiError(400, "Invalid or expired invite link", {}));
    mockAuth(acceptInvite);
    renderAt("/accept-invite?org=org-1&token=raw-token-abc");

    fireEvent.change(screen.getByLabelText(/Password/), {
      target: { value: "brandnew123" },
    });
    fireEvent.click(screen.getByText("Join workspace"));

    await waitFor(() =>
      expect(screen.getByText("Invalid or expired invite link")).toBeInTheDocument(),
    );
  });
});
