import { Fragment, type ReactNode } from "react";

// D5: a tiny, in-house renderer for the markdown subset LLM answers actually use —
// paragraphs, `#` headings (rendered as bold lines), ordered/unordered lists (one
// level of nesting), `---` rules, **bold**, *italic* / _italic_, `inline code` — plus
// the `[n]` citation marker as a first-class inline atom so callers can render it as
// a clickable button wherever it lands (inside bold, inside a list item, ...).
//
// Deliberately NOT a library (this codebase avoids UI deps) and deliberately never
// produces HTML strings: every node becomes a React element / text child, so model
// output like `<script>` is rendered as literal text, never parsed as markup.
//
// Every inline node keeps its [start, end) offset into the ORIGINAL source string, so
// callers can overlay annotations computed against the raw text (the per-sentence
// claim-check spans locate sentences — `**` and all — by indexOf on the raw answer).
//
// Streaming-safe: an opener with no matching closer yet (`**Chemi` mid-stream) is
// rendered literally until its closer arrives, so partial text never breaks layout.

export type InlineNode =
  | { kind: "text"; text: string; start: number; end: number }
  | { kind: "cite"; marker: number; raw: string; start: number; end: number }
  | { kind: "code"; text: string; start: number; end: number }
  | { kind: "strong" | "em"; children: InlineNode[]; start: number; end: number };

export type ListItem = {
  start: number;
  end: number;
  number: number | null;
  children: ListBlock | null;
};
export type ListBlock = { kind: "list"; ordered: boolean; items: ListItem[] };
export type Block =
  | { kind: "paragraph"; start: number; end: number }
  | { kind: "heading"; level: number; start: number; end: number }
  | { kind: "hr" }
  | ListBlock;

const HEADING_RE = /^(#{1,6})[ \t]+/;
const HR_RE = /^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$/;
const LIST_RE = /^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+/;

/** Pure: split `src` into blocks, each carrying absolute offsets into `src`. */
export function parseBlocks(src: string): Block[] {
  const blocks: Block[] = [];
  let para: { kind: "paragraph"; start: number; end: number } | null = null;
  let list: { block: ListBlock; indent: number } | null = null;

  const close = () => {
    para = null;
    list = null;
  };

  let offset = 0;
  for (const line of src.split("\n")) {
    const lineStart = offset;
    const lineEnd = offset + line.length;
    offset = lineEnd + 1;

    if (line.trim() === "") {
      close();
      continue;
    }
    if (HR_RE.test(line)) {
      close();
      blocks.push({ kind: "hr" });
      continue;
    }
    const heading = HEADING_RE.exec(line);
    if (heading) {
      close();
      blocks.push({
        kind: "heading",
        level: heading[1].length,
        start: lineStart + heading[0].length,
        end: lineEnd,
      });
      continue;
    }
    const item = LIST_RE.exec(line);
    if (item) {
      const indent = item[1].replace(/\t/g, "    ").length;
      const ordered = /\d/.test(item[2]);
      const number = ordered ? parseInt(item[2], 10) : null;
      const node: ListItem = {
        start: lineStart + item[0].length,
        end: lineEnd,
        number,
        children: null,
      };
      const current = list as { block: ListBlock; indent: number } | null;
      if (current && indent > current.indent && current.block.items.length > 0) {
        // Nested item: hang it off the previous top-level item (one level deep).
        const parent = current.block.items[current.block.items.length - 1];
        if (!parent.children) parent.children = { kind: "list", ordered, items: [] };
        parent.children.items.push(node);
        continue;
      }
      if (current && current.block.ordered === ordered) {
        current.block.items.push(node);
        continue;
      }
      close();
      const block: ListBlock = { kind: "list", ordered, items: [node] };
      blocks.push(block);
      list = { block, indent };
      continue;
    }
    // Plain text line.
    const currentList = list as { block: ListBlock; indent: number } | null;
    if (currentList && /^[ \t]/.test(line)) {
      // Indented continuation of the last list item (or its last nested item).
      const last = currentList.block.items[currentList.block.items.length - 1];
      const target = last.children ? last.children.items[last.children.items.length - 1] : last;
      target.end = lineEnd;
      continue;
    }
    const currentPara = para as { kind: "paragraph"; start: number; end: number } | null;
    if (currentPara) {
      currentPara.end = lineEnd;
      continue;
    }
    close();
    const p = { kind: "paragraph" as const, start: lineStart, end: lineEnd };
    blocks.push(p);
    para = p;
  }
  return blocks;
}

const isSpace = (ch: string | undefined) => ch === undefined || /\s/.test(ch);
const isWordChar = (ch: string | undefined) => ch !== undefined && /[A-Za-z0-9]/.test(ch);
const ESCAPABLE = "\\`*_[]#";

/** Find the closer for a `**`/`__` opened at `i`, or -1. */
function findDoubleCloser(src: string, i: number, b: number, ch: string): number {
  const pair = ch + ch;
  let j = src.indexOf(pair, i + 2);
  while (j !== -1 && j + 2 <= b) {
    if (j > i + 2 && !isSpace(src[j - 1]) && (ch === "*" || !isWordChar(src[j + 2]))) return j;
    j = src.indexOf(pair, j + 1);
  }
  return -1;
}

/** Find the closer for a single `*`/`_` opened at `i`, or -1. */
function findSingleCloser(src: string, i: number, b: number, ch: string): number {
  for (let k = i + 1; k < b; k++) {
    if (src[k] === "\n" && src[k + 1] === "\n") return -1;
    if (src[k] !== ch) continue;
    if (src[k + 1] === ch) {
      k++; // part of a `**` pair — not a single closer
      continue;
    }
    if (isSpace(src[k - 1])) continue;
    if (ch === "_" && isWordChar(src[k + 1])) continue;
    return k;
  }
  return -1;
}

/** Pure: parse `src[a:b]` into inline nodes with absolute offsets. */
export function parseInline(src: string, a: number, b: number): InlineNode[] {
  const nodes: InlineNode[] = [];
  let textStart = a;
  let i = a;

  const flush = (to: number) => {
    if (to > textStart) nodes.push({ kind: "text", text: src.slice(textStart, to), start: textStart, end: to });
  };

  while (i < b) {
    const ch = src[i];

    if (ch === "\\" && i + 1 < b && ESCAPABLE.includes(src[i + 1])) {
      flush(i);
      nodes.push({ kind: "text", text: src[i + 1], start: i, end: i + 2 });
      i += 2;
      textStart = i;
      continue;
    }

    if (ch === "[") {
      const m = /^\[(\d+)\]/.exec(src.slice(i, Math.min(b, i + 12)));
      if (m) {
        flush(i);
        const end = i + m[0].length;
        nodes.push({ kind: "cite", marker: parseInt(m[1], 10), raw: m[0], start: i, end });
        i = end;
        textStart = i;
        continue;
      }
    }

    if (ch === "`") {
      const j = src.indexOf("`", i + 1);
      if (j !== -1 && j < b && j > i + 1) {
        flush(i);
        nodes.push({ kind: "code", text: src.slice(i + 1, j), start: i, end: j + 1 });
        i = j + 1;
        textStart = i;
        continue;
      }
    }

    if (ch === "*" || ch === "_") {
      const leftOk = ch === "*" || !isWordChar(src[i - 1]);
      if (leftOk && src[i + 1] === ch && !isSpace(src[i + 2])) {
        const j = findDoubleCloser(src, i, b, ch);
        if (j !== -1) {
          flush(i);
          nodes.push({ kind: "strong", children: parseInline(src, i + 2, j), start: i, end: j + 2 });
          i = j + 2;
          textStart = i;
          continue;
        }
      } else if (leftOk && src[i + 1] !== ch && !isSpace(src[i + 1])) {
        const j = findSingleCloser(src, i, b, ch);
        if (j !== -1) {
          flush(i);
          nodes.push({ kind: "em", children: parseInline(src, i + 1, j), start: i, end: j + 1 });
          i = j + 1;
          textStart = i;
          continue;
        }
      }
      // Unclosed (e.g. mid-stream): fall through and render literally. Skip a whole
      // `**` run so its second char isn't retried as a single-`*` opener.
      i += src[i + 1] === ch ? 2 : 1;
      continue;
    }

    i++;
  }
  flush(b);
  return nodes;
}

/** A caller-supplied overlay over a [start, end) range of the source string. */
export type Annotation<T> = { start: number; end: number; data: T };

export type MarkdownProps<T> = {
  content: string;
  /** Render an `[n]` marker. Defaults to the muted mono literal. */
  renderCitation?: (marker: number, raw: string, key: string) => ReactNode;
  /** Non-overlapping ranges (sorted by start) to wrap, e.g. claim-check sentences. */
  annotations?: Annotation<T>[];
  /** Wrap the nodes inside an annotation; `isEnd` marks the piece ending the range. */
  renderAnnotation?: (data: T, children: ReactNode, key: string, isEnd: boolean) => ReactNode;
  className?: string;
};

function defaultCitation(_marker: number, raw: string, key: string): ReactNode {
  return (
    <span key={key} className="font-mono text-muted text-xs">
      {raw}
    </span>
  );
}

/** Split text nodes at annotation boundaries so every text node lies in ≤1 range. */
function splitAtBoundaries<T>(nodes: InlineNode[], anns: Annotation<T>[]): InlineNode[] {
  const cuts = anns.flatMap((r) => [r.start, r.end]);
  const out: InlineNode[] = [];
  for (const n of nodes) {
    if (n.kind !== "text") {
      out.push(n);
      continue;
    }
    const inner = cuts.filter((c) => c > n.start && c < n.end).sort((x, y) => x - y);
    let from = n.start;
    for (const c of [...inner, n.end]) {
      if (c > from) {
        out.push({ kind: "text", text: n.text.slice(from - n.start, c - n.start), start: from, end: c });
      }
      from = c;
    }
  }
  return out;
}

export function Markdown<T = unknown>({
  content,
  renderCitation = defaultCitation,
  annotations = [],
  renderAnnotation,
  className = "",
}: MarkdownProps<T>) {
  let keySeq = 0;
  const nextKey = () => `m${keySeq++}`;

  const renderNode = (n: InlineNode, annotate: boolean): ReactNode => {
    const key = nextKey();
    switch (n.kind) {
      case "text":
        return <Fragment key={key}>{n.text}</Fragment>;
      case "cite":
        return renderCitation(n.marker, n.raw, key);
      case "code":
        return (
          <code key={key} className="font-mono text-[0.9em] bg-bg border border-border rounded px-1">
            {n.text}
          </code>
        );
      case "strong":
        return (
          <strong key={key} className="font-semibold">
            {renderNodes(n.children, annotate)}
          </strong>
        );
      case "em":
        return <em key={key}>{renderNodes(n.children, annotate)}</em>;
    }
  };

  // Render a sibling list, grouping consecutive nodes that fall inside the same
  // annotation into one wrapper. A bold/italic node straddling an annotation boundary
  // can't be wrapped whole, so its children are annotated recursively instead.
  const renderNodes = (nodes: InlineNode[], annotate = true): ReactNode[] => {
    if (!annotate || annotations.length === 0 || !renderAnnotation) {
      return nodes.map((n) => renderNode(n, false));
    }
    const pieces = splitAtBoundaries(nodes, annotations);
    const out: ReactNode[] = [];
    let group: { ann: Annotation<T>; nodes: InlineNode[] } | null = null;

    const flushGroup = () => {
      if (!group) return;
      const g = group as { ann: Annotation<T>; nodes: InlineNode[] };
      const groupEnd = g.nodes[g.nodes.length - 1].end;
      out.push(renderAnnotation(g.ann.data, g.nodes.map((n) => renderNode(n, false)), nextKey(), groupEnd >= g.ann.end));
      group = null;
    };

    for (const n of pieces) {
      const ann = annotations.find((r) => n.start >= r.start && n.end <= r.end);
      if (ann) {
        const g = group as { ann: Annotation<T>; nodes: InlineNode[] } | null;
        if (g && g.ann === ann) g.nodes.push(n);
        else {
          flushGroup();
          group = { ann, nodes: [n] };
        }
        continue;
      }
      flushGroup();
      const straddles =
        (n.kind === "strong" || n.kind === "em") &&
        annotations.some((r) => r.start < n.end && r.end > n.start);
      if (straddles && (n.kind === "strong" || n.kind === "em")) {
        const Tag = n.kind === "strong" ? "strong" : "em";
        out.push(
          <Tag key={nextKey()} className={n.kind === "strong" ? "font-semibold" : undefined}>
            {renderNodes(n.children)}
          </Tag>,
        );
      } else {
        out.push(renderNode(n, false));
      }
    }
    flushGroup();
    return out;
  };

  const renderRange = (start: number, end: number) => renderNodes(parseInline(content, start, end));

  const renderList = (list: ListBlock): ReactNode => {
    const items = list.items.map((item) => (
      <li key={nextKey()} value={item.number ?? undefined} className="whitespace-pre-line">
        {renderRange(item.start, item.end)}
        {item.children && renderList(item.children)}
      </li>
    ));
    return list.ordered ? (
      <ol
        key={nextKey()}
        start={list.items[0]?.number ?? undefined}
        className="list-decimal pl-5 space-y-1"
      >
        {items}
      </ol>
    ) : (
      <ul key={nextKey()} className="list-disc pl-5 space-y-1">
        {items}
      </ul>
    );
  };

  const blocks = parseBlocks(content);
  return (
    <div className={`space-y-2 break-words ${className}`}>
      {blocks.map((block) => {
        switch (block.kind) {
          case "paragraph":
            return (
              <p key={nextKey()} className="whitespace-pre-line">
                {renderRange(block.start, block.end)}
              </p>
            );
          case "heading":
            return (
              <p
                key={nextKey()}
                role="heading"
                aria-level={block.level}
                className={`font-semibold ${block.level <= 2 ? "text-base" : ""}`}
              >
                {renderRange(block.start, block.end)}
              </p>
            );
          case "hr":
            return <hr key={nextKey()} className="border-border" />;
          case "list":
            return renderList(block);
        }
      })}
    </div>
  );
}
