import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { SignupPage } from "./SignupPage";

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function mockAuth(signup = vi.fn()) {
  vi.mocked(useAuth).mockReturnValue({
    user: null,
    loading: false,
    login: vi.fn(),
    signup,
    acceptInvite: vi.fn(),
    logout: vi.fn(),
  });
  return signup;
}

function renderSignupPage() {
  return render(
    <MemoryRouter>
      <SignupPage />
    </MemoryRouter>,
  );
}

describe("SignupPage", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReset();
  });

  it("labels the name field as optional and submits it when filled", async () => {
    const signup = mockAuth();
    renderSignupPage();

    expect(screen.getByText("Full name (optional)")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Full name (optional)"), {
      target: { value: "Uma Reviewer" },
    });
    fireEvent.change(screen.getByLabelText("Organization name"), {
      target: { value: "Acme" },
    });
    fireEvent.change(screen.getByLabelText("Work email"), {
      target: { value: "uma@acme.com" },
    });
    fireEvent.change(screen.getByLabelText(/Password/), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByText("Create account"));

    await waitFor(() =>
      expect(signup).toHaveBeenCalledWith(
        "uma@acme.com",
        "password123",
        "Acme",
        "Uma Reviewer",
      ),
    );
  });

  it("submits an empty name when the field is left blank", async () => {
    const signup = mockAuth();
    renderSignupPage();

    fireEvent.change(screen.getByLabelText("Organization name"), {
      target: { value: "Acme" },
    });
    fireEvent.change(screen.getByLabelText("Work email"), {
      target: { value: "uma@acme.com" },
    });
    fireEvent.change(screen.getByLabelText(/Password/), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByText("Create account"));

    await waitFor(() =>
      expect(signup).toHaveBeenCalledWith("uma@acme.com", "password123", "Acme", ""),
    );
  });
});
