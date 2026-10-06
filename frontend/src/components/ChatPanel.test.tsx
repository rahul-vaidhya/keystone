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
import { evalsApi } from "../services/evalsService";
import { useAuth } from "../hooks/useAuth";
import { ChatPanel } from "./ChatPanel";

vi.mock("../services/chatService", () => ({
  chatApi: {
    streamAsk: vi.fn(),
    getTrace: vi.fn(),
    listMessages: vi.fn(),
    submitFeedback: vi.fn(),
  },
}));

vi.mock("../services/evalsService", () => ({
  evalsApi: {
    addGoldenQuestion: vi.fn(),
    listGoldenQuestions: vi.fn(),
  },
}));

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function mockUser(role: "owner" | "admin" | "member" = "member") {
  vi.mocked(useAuth).mockReturnValue({
    user: {
      id: "u-1",
      org_id: "org-1",
      email: "u@test.com",
      name: null,
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
    uploader_email: null,
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
    my_feedback: null,
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
    page_start: null,
    page_end: null,
    citation_type: "chunk",
    section_id: null,
    heading: null,
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
    weak_evidence: false,
    ...overrides,
  };
}

describe("ChatPanel", () => {
  beforeEach(() => {
    vi.mocked(chatApi.streamAsk).mockReset();
    vi.mocked(chatApi.getTrace).mockReset();
    vi.mocked(chatApi.listMessages).mockReset();
    vi.mocked(chatApi.listMessages).mockResolvedValue([]);
    vi.mocked(chatApi.submitFeedback).mockReset();
    vi.mocked(chatApi.submitFeedback).mockResolvedValue(undefined);
    vi.mocked(evalsApi.addGoldenQuestion).mockReset();
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

  // Reranker-score confidence gate (P1): weak_evidence indicator.
  describe("weak evidence indicator", () => {
    it("shows the 'Weak evidence' badge when the response carries weak_evidence: true", async () => {
      vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
        callbacks.onDone(
          makeDoneResponse({
            answer: "The available sources don't contain a strong match for this question.",
            citations: [],
            weak_evidence: true,
          }),
        );
        return () => {};
      });

      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      const input = screen.getByPlaceholderText("Ask a question…");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.submit(input.closest("form")!);

      await waitFor(() => expect(screen.getByText("Weak evidence")).toBeInTheDocument());
    });

    it("does not show the 'Weak evidence' badge on a normal grounded answer", async () => {
      vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
        callbacks.onDone(makeDoneResponse({ weak_evidence: false }));
        return () => {};
      });

      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      const input = screen.getByPlaceholderText("Ask a question…");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.submit(input.closest("form")!);

      await waitFor(() => expect(screen.getByText("[1]")).toBeInTheDocument());
      expect(screen.queryByText("Weak evidence")).not.toBeInTheDocument();
    });
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

  it("curates a golden question from the debug panel and shows the result", async () => {
    mockUser("admin");
    vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
      callbacks.onDone(makeDoneResponse({ message_id: "msg-1" }));
      return () => {};
    });
    vi.mocked(chatApi.getTrace).mockResolvedValue(makeTrace());
    vi.mocked(evalsApi.addGoldenQuestion).mockResolvedValue({
      id: "gq-1",
      notebook_id: "nb-1",
      source_message_id: "msg-1",
      question: "test question",
      reference_answer: "This is the answer [1]",
      reference_contexts: ["relevant text"],
      status: "active",
      created_by: "u-1",
      created_at: "2026-01-01T00:00:00Z",
    });

    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => expect(screen.getByText("Debug")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Debug"));
    await waitFor(() => expect(screen.getByText("Hits (1)")).toBeInTheDocument());

    const addButton = screen.getByRole("button", { name: "Add to golden set" });
    fireEvent.click(addButton);

    expect(evalsApi.addGoldenQuestion).toHaveBeenCalledWith("msg-1");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Added ✓" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Added ✓" })).toBeDisabled();
  });

  it("hides the golden-set button for a non-admin member (nested inside admin-only Debug panel)", async () => {
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
    expect(screen.queryByRole("button", { name: "Add to golden set" })).not.toBeInTheDocument();
  });

  describe("per-sentence citation check", () => {
    const answer =
      "Atoms want eight electrons [1]. Bananas are yellow [1]. This has no source at all.";
    const checks = [
      {
        sentence: "Atoms want eight electrons [1].",
        citations: [1],
        lexical: 0.62,
        semantic: 0.71,
        score: 0.665,
        status: "supported" as const,
      },
      {
        sentence: "Bananas are yellow [1].",
        citations: [1],
        lexical: 0,
        semantic: 0.12,
        score: 0.06,
        status: "weak" as const,
      },
      {
        sentence: "This has no source at all.",
        citations: [],
        lexical: null,
        semantic: null,
        score: null,
        status: "uncited" as const,
      },
    ];

    it("renders the summary line and flags weak/uncited sentences after a streamed answer", async () => {
      vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
        callbacks.onDone(makeDoneResponse({ answer, claim_checks: checks }));
        return () => {};
      });
      const { container } = render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      const input = screen.getByPlaceholderText("Ask a question…");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.submit(input.closest("form")!);

      await waitFor(() =>
        expect(screen.getByTestId("claim-check-summary")).toHaveTextContent(
          "Claim check: 1 supported · 1 weak · 1 uncited",
        ),
      );
      const weak = container.querySelector('[data-claim-status="weak"]')!;
      expect(weak).toHaveTextContent("Bananas are yellow");
      expect(weak.getAttribute("title")).toContain("lexical 0.00");
      expect(weak.getAttribute("title")).toContain("semantic 0.12");
      expect(weak.getAttribute("title")).toContain("combined 0.06");
      const uncited = container.querySelector('[data-claim-status="uncited"]')!;
      expect(uncited).toHaveTextContent("This has no source at all.");
      expect(container.querySelector('[data-claim-status="supported"]')).toBeNull();
      // Existing [n] citation buttons still render (one per marker occurrence).
      expect(screen.getAllByRole("button", { name: "[1]" })).toHaveLength(2);
    });

    it("hydrates claim checks from history", async () => {
      vi.mocked(chatApi.listMessages).mockResolvedValue([
        makeHistoryMessage({
          id: "h2",
          role: "assistant",
          content: answer,
          citations: [makeCitation()],
          claim_checks: checks,
        }),
      ]);
      vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      await waitFor(() =>
        expect(screen.getByTestId("claim-check-summary")).toHaveTextContent(
          "1 supported · 1 weak · 1 uncited",
        ),
      );
    });

    it("renders no summary when claim checks are absent", async () => {
      vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
        callbacks.onDone(makeDoneResponse());
        return () => {};
      });
      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      const input = screen.getByPlaceholderText("Ask a question…");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.submit(input.closest("form")!);
      await waitFor(() => expect(screen.getByText("[1]")).toBeInTheDocument());
      expect(screen.queryByTestId("claim-check-summary")).not.toBeInTheDocument();
    });
  });

  it("shows sparse score and the term-contribution table in the admin Debug panel", async () => {
    mockUser("admin");
    vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
      callbacks.onDone(makeDoneResponse({ message_id: "msg-sparse" }));
      return () => {};
    });
    vi.mocked(chatApi.getTrace).mockResolvedValue(
      makeTrace({
        hits: [
          {
            index: 1,
            document_id: "doc-1",
            chunk_id: "chunk-1",
            char_start: 0,
            char_end: 13,
            content: "relevant text",
            distance: null,
            rerank_score: 0.91,
            sparse_score: 1.234,
            sparse_explanation: [
              { term: "octet", zone: "body", tf: 2, idf: 1.5, weight: 0.8 },
              { term: "rule", zone: "heading", tf: 1, idf: 0.9, weight: 0.434 },
            ],
          },
        ],
      }),
    );

    render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
    const input = screen.getByPlaceholderText("Ask a question…");
    fireEvent.change(input, { target: { value: "test" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(screen.getByText("Debug")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Debug"));

    const table = await screen.findByRole("table", { name: "Term contributions for hit 1" });
    expect(table).toHaveTextContent("octet");
    expect(table).toHaveTextContent("heading");
    expect(table).toHaveTextContent("1.500");
    expect(table).toHaveTextContent("0.8000");
    expect(screen.getByText(/sparse 1\.234/)).toBeInTheDocument();
    expect(screen.getByText(/rerank 0\.910/)).toBeInTheDocument();
  });

  // Finding A (Medium, UX audit): static starter-question chips on the empty state.
  describe("starter question chips (Finding A)", () => {
    it("renders starter chips when the notebook has documents and no messages yet", async () => {
      vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);

      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Summarize the key points in these documents" }),
        ).toBeInTheDocument(),
      );
      expect(
        screen.getByRole("button", { name: "What are the main topics covered?" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", {
          name: "What definitions or important terms are explained here?",
        }),
      ).toBeInTheDocument();
    });

    it("does not render starter chips when the notebook has zero documents", async () => {
      vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
      render(<ChatPanel notebookId="nb-1" documents={[]} />);

      await waitFor(() =>
        expect(screen.getByText(/This notebook has no documents yet/)).toBeInTheDocument(),
      );
      expect(
        screen.queryByRole("button", { name: "Summarize the key points in these documents" }),
      ).not.toBeInTheDocument();
    });

    it("does not render starter chips once a conversation has messages", async () => {
      vi.mocked(chatApi.listMessages).mockResolvedValue([
        makeHistoryMessage({ id: "h1", role: "user", content: "already asked" }),
      ]);
      vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);

      await waitFor(() => expect(screen.getByText("already asked")).toBeInTheDocument());
      expect(
        screen.queryByRole("button", { name: "Summarize the key points in these documents" }),
      ).not.toBeInTheDocument();
    });

    it("clicking a chip immediately submits that question via streamAsk", async () => {
      vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});
      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);

      const chip = await screen.findByRole("button", {
        name: "What are the main topics covered?",
      });
      fireEvent.click(chip);

      await waitFor(() =>
        expect(chatApi.streamAsk).toHaveBeenCalledWith(
          { notebook_id: "nb-1", query: "What are the main topics covered?" },
          expect.objectContaining({ onToken: expect.any(Function), onDone: expect.any(Function) }),
        ),
      );
      expect(screen.getByText("What are the main topics covered?")).toBeInTheDocument();
    });
  });

  // Finding B (Low, UX audit): copy-to-clipboard + thumbs up/down feedback controls.
  describe("copy and feedback controls (Finding B)", () => {
    beforeEach(() => {
      Object.assign(navigator, {
        clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
      });
    });

    it("copies the final assistant message content to the clipboard", async () => {
      vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
        callbacks.onDone(makeDoneResponse({ answer: "See source [1]", citations: [makeCitation()] }));
        return () => {};
      });

      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      const input = screen.getByPlaceholderText("Ask a question…");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.submit(input.closest("form")!);

      const copyButton = await screen.findByRole("button", { name: "Copy answer" });
      fireEvent.click(copyButton);

      await waitFor(() =>
        expect(navigator.clipboard.writeText).toHaveBeenCalledWith("See source [1]"),
      );
      await waitFor(() => expect(screen.getByText("Copied")).toBeInTheDocument());
    });

    it("toggles thumbs up/down as a mutually exclusive tri-state, purely client-side", async () => {
      vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
        callbacks.onDone(makeDoneResponse());
        return () => {};
      });

      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      const input = screen.getByPlaceholderText("Ask a question…");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.submit(input.closest("form")!);

      const up = await screen.findByRole("button", { name: "Good response" });
      const down = screen.getByRole("button", { name: "Bad response" });

      expect(up).toHaveAttribute("aria-pressed", "false");
      expect(down).toHaveAttribute("aria-pressed", "false");

      fireEvent.click(up);
      expect(up).toHaveAttribute("aria-pressed", "true");
      expect(down).toHaveAttribute("aria-pressed", "false");

      fireEvent.click(down);
      expect(up).toHaveAttribute("aria-pressed", "false");
      expect(down).toHaveAttribute("aria-pressed", "true");

      fireEvent.click(down);
      expect(up).toHaveAttribute("aria-pressed", "false");
      expect(down).toHaveAttribute("aria-pressed", "false");
    });

    it("shows copy/feedback controls for a non-admin member too (not gated on role)", async () => {
      mockUser("member");
      vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
        callbacks.onDone(makeDoneResponse());
        return () => {};
      });

      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      const input = screen.getByPlaceholderText("Ask a question…");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.submit(input.closest("form")!);

      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Copy answer" })).toBeInTheDocument(),
      );
      expect(screen.getByRole("button", { name: "Good response" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Bad response" })).toBeInTheDocument();
    });

    it("calls chatApi.submitFeedback with the message id and rating when a thumb is clicked", async () => {
      vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
        callbacks.onDone(makeDoneResponse({ message_id: "msg-42" }));
        return () => {};
      });

      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      const input = screen.getByPlaceholderText("Ask a question…");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.submit(input.closest("form")!);

      const up = await screen.findByRole("button", { name: "Good response" });
      fireEvent.click(up);
      await waitFor(() =>
        expect(chatApi.submitFeedback).toHaveBeenCalledWith("msg-42", { rating: "up" }),
      );

      const down = screen.getByRole("button", { name: "Bad response" });
      fireEvent.click(down);
      await waitFor(() =>
        expect(chatApi.submitFeedback).toHaveBeenCalledWith("msg-42", { rating: "down" }),
      );
    });

    it("does not break the UI when submitFeedback rejects (fire-and-forget)", async () => {
      vi.mocked(chatApi.submitFeedback).mockRejectedValue(new Error("network error"));
      vi.mocked(chatApi.streamAsk).mockImplementation((_params, callbacks) => {
        callbacks.onDone(makeDoneResponse());
        return () => {};
      });

      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);
      const input = screen.getByPlaceholderText("Ask a question…");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.submit(input.closest("form")!);

      const up = await screen.findByRole("button", { name: "Good response" });
      fireEvent.click(up);

      // Optimistic local state still applies even though the network call rejects.
      await waitFor(() => expect(up).toHaveAttribute("aria-pressed", "true"));
    });

    it("seeds the feedback button state from history's my_feedback on mount", async () => {
      vi.mocked(chatApi.listMessages).mockResolvedValue([
        makeHistoryMessage({
          id: "h1",
          role: "assistant",
          content: "earlier answer [1]",
          citations: [makeCitation()],
          my_feedback: "up",
        }),
      ]);
      vi.mocked(chatApi.streamAsk).mockReturnValue(() => {});

      render(<ChatPanel notebookId="nb-1" documents={[makeDoc()]} />);

      const up = await screen.findByRole("button", { name: "Good response" });
      const down = screen.getByRole("button", { name: "Bad response" });
      expect(up).toHaveAttribute("aria-pressed", "true");
      expect(down).toHaveAttribute("aria-pressed", "false");
    });
  });
});
