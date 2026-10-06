// Highlights whole-word occurrences of `words` (the backend's surface forms whose Porter
// stem matched a query term) inside `text`. Parser markdown noise ("### Page N" lines,
// heading hashes) is stripped first so snippets read as plain text.
const PAGE_MARKER_LINE = /^#{1,6}\s*Page\s+\d+\s*$/;
const HEADING_PREFIX = /^#{1,6}\s+/;

export function cleanParserMarkdown(content: string): string {
  return content
    .split("\n")
    .filter((line) => !PAGE_MARKER_LINE.test(line.trim()))
    .map((line) => line.replace(HEADING_PREFIX, ""))
    .join("\n")
    .trim();
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function HighlightedText({ text, words }: { text: string; words: string[] }) {
  const clean = cleanParserMarkdown(text);
  const unique = [...new Set(words.filter(Boolean))].sort((a, b) => b.length - a.length);
  if (unique.length === 0) return <>{clean}</>;
  const re = new RegExp(
    `(?<![\\p{L}\\p{N}])(${unique.map(escapeRegExp).join("|")})(?![\\p{L}\\p{N}])`,
    "gu",
  );
  const parts = clean.split(re);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="bg-transparent text-accent font-semibold">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}
