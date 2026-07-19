import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import type {
  ChatHistoryMessage,
  ChatResponse,
  MessageTrace,
  ResolvedCitation,
} from "../types/chat";
import type { Document } from "../types/documents";
import { chatApi } from "../services/chatService";
import { useAuth } from "../hooks/useAuth";
import { ChatPanel } from "./ChatPanel";

vi.mock("../services/chatService", () => ({
  chatApi: {
    streamAsk: vi.fn(),
    getTrace: vi.fn(),
    listMessages: vi.fn(),
  },
}));

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function mockUser(role: "owner" | "admin" | "member" = "member") {
  vi.mocked(useAuth).mockReturnValue({
    user: {
      id: "u-1",
      org_id: "org-1",
      email: "u@test.com",
      role,
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

function makeTrace(overrides: Partial<MessageTrace> = {}): MessageTrace {
  return {
    id: "trace-1",
    message_id: "msg-1",
    hits: [
      {
        index: 1,
        document_id: "doc-1",
        chunk_id: "chunk-1",
        char_start: 0,
        char_end: 13,
        content: "relevant text",
        distance: 0.12,
      },
    ],
    final_prompt: "[system]\n...\n\n[user]\n...",
    raw_output: "This is the answer [1]",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeDoc(overrides: Partial<Document> = {}): Document {
  return {
    id: "doc-1",
    org_id: "org-1",
    folder_id: null,
    title: "report.pdf",
    storage_key: "key",
    mime_type: "application/pdf",
    byte_size: 100,
    checksum: "abc",
    page_count: 1,
    language: "en",
    status: "READY",
    failed_stage: null,
    error_detail: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeHistoryMessage(overrides: Partial<ChatHistoryMessage> = {}): ChatHistoryMessage {
  return {
    id: "hist-1",
    conversation_id: "conv-1",
    role: "user",
    content: "prior question",
    citations: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeCitation(overrides: Partial<ResolvedCitation> = {}): ResolvedCitation {
  return {
    marker: 1,
    document_id: "doc-1",
    chunk_id: "chunk-1",
    char_start: 0,
    char_end: 13,
    content: "relevant text",
    ...overrides,
  };
}

function makeDoneResponse(overrides: Partial<ChatResponse> = {}): ChatResponse {
  return {
    correlation_id: "corr-1",
    conversation_id: "conv-1",
    message_id: "msg-1",
    notebook_id: "nb-1",
    query: "test question",
    answer: "This is the answer [1]",
    citations: [makeCitation()],
    model: "fake-llm",
    ...overrides,
  };
}

describe("ChatPanel", () => {
  beforeEach(() => {
    vi.mocked(chatApi.streamAsk).mockReset();
    vi.mocked(chatApi.getTrace).mockReset();
    vi.mocked(chatApi.listMessages).mockReset();
    vi.mocked(chatApi.listMessages).mockResolvedValue([]);
    mockUser("member");
  });

  it("renders an empty chat input and no messages initially", async () => {
    vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
    expect(screen.getByPlaceholderText("Ask a question…")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/Ask a question about the documents/)).toBeInTheDocument(),
    );
  });

  it("disables the input and Ask button, and shows the no-documents empty state, when the notebook has zero attached documents", async () => {
    vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
    render(<ChatPanel notebookId="nb-1" documents={[]} />);

    const input = await screen.findByPlaceholderText(
      "Attach a document to this notebook before asking a question",
    );
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    await waitFor(() =>
      expect(
        screen.getByText(/This notebook has no documents yet/),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Ask a question about the documents/)).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.submit(input.closest("form")!);
    expect(chatApi.streamAsk).not.toHaveBeenCalled();
  });

  it("enables the input and shows the generic ask prompt once at least one document is attached, regardless of its ingestion status", async () => {
    vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
    render(<ChatPanel notebookId="nb-1" documents={[makeDoc({ status: "PARSING" })]} />);

    const input = await screen.findByPlaceholderText("Ask a question…");
    expect(input).not.toBeDisabled();
    await waitFor(() =>
      expect(screen.getByText(/Ask a question about the documents/)).toBeInTheDocument(),
    );
  });

  it("fetches and renders prior history as messages on mount, with clickable citations", async () => {
    vi.mocked(chatApi.listMessages).mockResolvedValue([
      makeHistoryMessage({ id: "h1", role: "user", content: "earlier question", citations: null }),
      makeHistoryMessage({
        id: "h2",
        role: "assistant",
        content: "earlier answer [1]",
        citations: [makeCitation()],
      }),
    ]);
    vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});

    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);

    expect(chatApi.listMessages).toHaveBeenCalledWith("nb-1");
    await waitFor(() => expect(screen.getByText("earlier question")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "[1]" })).toBeInTheDocument();
  });

  it("refetches and clears prior messages when notebookId changes", async () => {
    vi.mocked(chatApi.listMessages).mockImplementation((notebookId: string) =>
      Promise.resolve(
        notebookId === "nb-1"
          ? [makeHistoryMessage({ id: "h1", content: "message in nb-1" })]
          : [makeHistoryMessage({ id: "h2", content: "message in nb-2" })],
      ),
    );
    vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});

    const { rerender } = render(<ChatPanel notebookId="nb-1" documents={[]} />);
    await waitFor(() => expect(screen.getByText("message in nb-1")).toBeInTheDocument());

    rerender(<ChatPanel notebookId="nb-2" documents={[]} />);

    await waitFor(() => expect(screen.getByText("message in nb-2")).toBeInTheDocument());
    expect(screen.queryByText("message in nb-1")).not.toBeInTheDocument();
    expect(chatApi.listMessages).toHaveBeenCalledWith("nb-2");
  });

  it("calls chatApi.streamAsk with the notebook_id and query on submit", async () => {
    vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);

    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "What is onboarding?" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() =>
      expect(chatApi.streamAsk).toHaveBeenCalledWith(
        { notebook_id: "nb-1", query: "What is onboarding?" },
        expect.objectContaining({ onToken: expect.any(Function), onDone: expect.any(Function) }),
      ),
    );
  });

  it("displays streamed tokens as they arrive", async () => {
    vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
      callbacks.onToken("Hello ");
      callbacks.onToken("world");
      return () => {};
    });

    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "hi" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => expect(screen.getByText("Hello world")).toBeInTheDocument());
  });

  it("renders citation markers as clickable buttons after onDone", async () => {
    vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
      callbacks.onDone(makeDoneResponse({ answer: "See source [1]", citations: [makeCitation()] }));
      return () => {};
    });

    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => expect(screen.getByText("[1]")).toBeInTheDocument());
    // [1] is a button
    expect(screen.getByRole("button", { name: "[1]" })).toBeInTheDocument();
  });

  it("opens the citation panel with the source content when a citation is clicked", async () => {
    const citation = makeCitation({ content: "relevant text here" });
    vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
      callbacks.onDone(makeDoneResponse({ answer: "See [1]", citations: [citation] }));
      return () => {};
    });

    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => expect(screen.getByRole("button", { name: "[1]" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "[1]" }));

    await waitFor(() =>
      expect(screen.getByText("relevant text here")).toBeInTheDocument(),
    );
    expect(screen.getByText("Source [1]")).toBeInTheDocument();
  });

  it("shows an error message when onError fires", async () => {
    vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
      callbacks.onError("LLM unavailable");
      return () => {};
    });

    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() =>
      expect(screen.getByText("Error: LLM unavailable")).toBeInTheDocument(),
    );
  });

  it("hides the Debug toggle for a non-admin member", async () => {
    mockUser("member");
    vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
      callbacks.onDone(makeDoneResponse());
      return () => {};
    });

    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => expect(screen.getByText("[1]")).toBeInTheDocument());
    expect(screen.queryByText("Debug")).not.toBeInTheDocument();
  });

  it("shows the Debug toggle for an admin and fetches the trace on click", async () => {
    mockUser("admin");
    vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
      callbacks.onDone(makeDoneResponse());
      return () => {};
    });
    vi.mocked(chatApi.getTrace).mockResolvedValue(makeTrace());

    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => expect(screen.getByText("Debug")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Debug"));

    expect(chatApi.getTrace).toHaveBeenCalledWith("msg-1");
    await waitFor(() =>
      expect(screen.getByText("This is the answer [1]")).toBeInTheDocument(),
    );
    expect(screen.getByText("Hits (1)")).toBeInTheDocument();
  });
});
