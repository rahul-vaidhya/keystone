import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import type { ChatResponse, ResolvedCitation } from "../types/chat";
import type { Document } from "../types/documents";
import { chatApi } from "../services/chatService";
import { ChatPanel } from "./ChatPanel";

vi.mock("../services/chatService", () => ({
  chatApi: {
    streamAsk: vi.fn(),
  },
}));

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
  });

  it("renders an empty chat input and no messages initially", () => {
    vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
    render(<ChatPanel notebookId="nb-1" documents={[]} />);
    expect(screen.getByPlaceholderText("Ask a question…")).toBeInTheDocument();
    expect(screen.getByText(/Ask a question about the documents/)).toBeInTheDocument();
  });

  it("calls chatApi.streamAsk with the notebook_id and query on submit", async () => {
    vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
    render(<ChatPanel notebookId="nb-1" documents={[]} />);

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

    render(<ChatPanel notebookId="nb-1" documents={[]} />);
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

    render(<ChatPanel notebookId="nb-1" documents={[]} />);
    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() =>
      expect(screen.getByText("Error: LLM unavailable")).toBeInTheDocument(),
    );
  });
});
