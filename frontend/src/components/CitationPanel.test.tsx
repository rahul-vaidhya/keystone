import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { CitationPanel } from "./CitationPanel";
import type { ResolvedCitation } from "../types/chat";
import type { Document } from "../types/documents";

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

const documents: Document[] = [
  {
    id: "doc-1",
    org_id: "org-1",
    folder_id: null,
    title: "Sample Document",
    storage_key: null,
    mime_type: null,
    byte_size: null,
    checksum: null,
    page_count: null,
    language: null,
    status: "READY",
    failed_stage: null,
    error_detail: null,
    uploader_email: null,
    created_at: "2026-01-01T00:00:00Z",
  },
];

describe("CitationPanel", () => {
  it("still renders the char-offset line for every citation", () => {
    render(
      <CitationPanel citation={makeCitation()} documents={documents} onClose={vi.fn()} />,
    );
    expect(screen.getByText("chars 0–13")).toBeTruthy();
  });

  it("renders 'Page N' when page_start === page_end", () => {
    render(
      <CitationPanel
        citation={makeCitation({ page_start: 4, page_end: 4 })}
        documents={documents}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Page 4")).toBeTruthy();
  });

  it("renders 'Pages N–M' when the citation spans more than one page", () => {
    render(
      <CitationPanel
        citation={makeCitation({ page_start: 3, page_end: 5 })}
        documents={documents}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Pages 3–5")).toBeTruthy();
  });

  it("renders 'Page N' when page_end is null but page_start is present", () => {
    render(
      <CitationPanel
        citation={makeCitation({ page_start: 7, page_end: null })}
        documents={documents}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Page 7")).toBeTruthy();
  });

  it("renders no page line at all when page_start is null (older docs / no section)", () => {
    render(
      <CitationPanel
        citation={makeCitation({ page_start: null, page_end: null })}
        documents={documents}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText(/^Page/)).toBeNull();
    // The char-offset fallback line is still there, unchanged.
    expect(screen.getByText("chars 0–13")).toBeTruthy();
  });

  it("renders a section citation without char offsets, showing the heading instead", () => {
    render(
      <CitationPanel
        citation={makeCitation({
          citation_type: "section",
          chunk_id: null,
          char_start: null,
          char_end: null,
          section_id: "sec-1",
          heading: "Onboarding",
          content: "section summary extract",
        })}
        documents={documents}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Section: Onboarding")).toBeTruthy();
    expect(screen.queryByText(/^chars/)).toBeNull();
    expect(screen.queryByText(/^Page/)).toBeNull();
    expect(screen.getByText("section summary extract")).toBeTruthy();
  });

  it("renders a section citation with no heading and no char-offset/heading line at all", () => {
    render(
      <CitationPanel
        citation={makeCitation({
          citation_type: "section",
          chunk_id: null,
          char_start: null,
          char_end: null,
          section_id: "sec-1",
          heading: null,
        })}
        documents={documents}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText(/^chars/)).toBeNull();
    expect(screen.queryByText(/^Section:/)).toBeNull();
  });
});
