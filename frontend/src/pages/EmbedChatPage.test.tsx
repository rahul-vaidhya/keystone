import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { embedApi } from "../services/embedService";
import type { EmbedConfig, EmbedSSEEvent } from "../types/embed";
import { EmbedChatPage } from "./EmbedChatPage";

vi.mock("../services/embedService", () => ({
  embedApi: {
    getPublicConfig: vi.fn(),
    streamPublicChat: vi.fn(),
  },
}));

function makeConfig(overrides: Partial<EmbedConfig> = {}): EmbedConfig {
  return {
    widget_name: "Support bot",
    notebook_name: "Product Docs",
    ...overrides,
  };
}

function makeDoneEvent(
  overrides: Partial<Extract<EmbedSSEEvent, { type: "done" }>> = {},
): Extract<EmbedSSEEvent, { type: "done" }> {
  return {
    type: "done",
    correlation_id: "corr-1",
    conversation_id: "conv-1",
    message_id: "msg-1",
    notebook_id: "nb-1",
    query: "What is this?",
    answer: "This is the answer [1]",
    citations: [],
    model: "test-model",
    ...overrides,
  };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/embed" element={<EmbedChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("EmbedChatPage", () => {
  beforeEach(() => {
    vi.mocked(embedApi.getPublicConfig).mockReset();
    vi.mocked(embedApi.streamPublicChat).mockReset();
  });

  it("renders the widget name and notebook name after the config fetch resolves", async () => {
    vi.mocked(embedApi.getPublicConfig).mockResolvedValue(makeConfig());

    renderAt("/embed?org=org-1&widget=w-1&parent=https%3A%2F%2Fexample.com");

    await waitFor(() => expect(screen.getByText("Support bot")).toBeInTheDocument());
    expect(screen.getByText("Product Docs")).toBeInTheDocument();
    expect(embedApi.getPublicConfig).toHaveBeenCalledWith("org-1", "w-1");
  });

  it("shows the unavailable state when the config fetch fails", async () => {
    vi.mocked(embedApi.getPublicConfig).mockRejectedValue(new Error("Not found"));

    renderAt("/embed?org=org-1&widget=w-1&parent=https%3A%2F%2Fexample.com");

    await waitFor(() =>
      expect(screen.getByText("This chatbot is unavailable.")).toBeInTheDocument(),
    );
  });

  it("shows the unavailable state (and never fetches) when org/widget query params are missing", async () => {
    renderAt("/embed");

    expect(screen.getByText("This chatbot is unavailable.")).toBeInTheDocument();
    expect(embedApi.getPublicConfig).not.toHaveBeenCalled();
  });

  it("submits a query with the parent_origin from the URL and renders the streamed answer", async () => {
    vi.mocked(embedApi.getPublicConfig).mockResolvedValue(makeConfig());
    vi.mocked(embedApi.streamPublicChat).mockImplementation((_org, _widgetId, _body, callbacks) => {
      callbacks.onToken("This is ");
      callbacks.onToken("the answer [1]");
      callbacks.onDone(makeDoneEvent({ answer: "This is the answer [1]" }));
      return () => {};
    });

    renderAt("/embed?org=org-1&widget=w-1&parent=https%3A%2F%2Fexample.com");
    await waitFor(() => expect(screen.getByText("Support bot")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Ask a question…"), {
      target: { value: "What is this?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(embedApi.streamPublicChat).toHaveBeenCalledWith(
        "org-1",
        "w-1",
        { query: "What is this?", parent_origin: "https://example.com" },
        expect.objectContaining({
          onToken: expect.any(Function),
          onDone: expect.any(Function),
          onError: expect.any(Function),
        }),
      ),
    );
    await waitFor(() =>
      expect(screen.getByText(/This is the answer/)).toBeInTheDocument(),
    );
  });

  it("renders a friendly error bubble when the stream reports an error", async () => {
    vi.mocked(embedApi.getPublicConfig).mockResolvedValue(makeConfig());
    vi.mocked(embedApi.streamPublicChat).mockImplementation((_org, _widgetId, _body, callbacks) => {
      callbacks.onError("Origin not allowed for this widget", 403);
      return () => {};
    });

    renderAt("/embed?org=org-1&widget=w-1&parent=https%3A%2F%2Fexample.com");
    await waitFor(() => expect(screen.getByText("Support bot")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Ask a question…"), {
      target: { value: "What is this?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(
        screen.getByText("Sorry — something went wrong. Please try again."),
      ).toBeInTheDocument(),
    );
  });

  it("shows the rate-limit-specific message on a 429 error", async () => {
    vi.mocked(embedApi.getPublicConfig).mockResolvedValue(makeConfig());
    vi.mocked(embedApi.streamPublicChat).mockImplementation((_org, _widgetId, _body, callbacks) => {
      callbacks.onError("Widget rate limit exceeded", 429);
      return () => {};
    });

    renderAt("/embed?org=org-1&widget=w-1&parent=https%3A%2F%2Fexample.com");
    await waitFor(() => expect(screen.getByText("Support bot")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Ask a question…"), {
      target: { value: "What is this?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(
        screen.getByText("You're sending messages too quickly — please wait a moment."),
      ).toBeInTheDocument(),
    );
  });
});
