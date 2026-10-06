// A technical term with a one-sentence hover explanation (native `title` tooltip, dotted
// underline as the affordance). Text content stays exactly `children`, so labels and
// accessible names are unchanged.
export function Tip({ tip, children }: { tip: string; children: React.ReactNode }) {
  return (
    <span title={tip} className="underline decoration-dotted decoration-muted underline-offset-2 cursor-help">
      {children}
    </span>
  );
}

// One short sentence per IR term, shared by the Search page panels.
export const TIPS = {
  N: "Number of indexed units — every chunk of the notebook's documents is one document.",
  dictionary: "Distinct index terms (Porter stems) in the inverted index's dictionary.",
  postings: "A postings list holds, for one term, every chunk containing it (with positions).",
  avgPostings: "Mean postings-list length over the dictionary, i.e. the mean df.",
  zones: "Separate indexed fields: the section heading and the chunk body each get their own postings.",
  df: "Document frequency: how many chunks contain the term.",
  idf: "Inverse document frequency log₁₀(N/df): rare terms get high weight, common terms near 0.",
  champions: "Champion list: the r postings with the highest tf, used as a small candidate set for fast top-K.",
  idfThreshold: "Index elimination: query terms with idf at or below this threshold are skipped as too common.",
  zoneWeights: "Weighted zone scoring: the final score is Σ zone weight × that zone's score.",
  bm25: "Okapi BM25: probabilistic scoring with tf saturation (k₁) and document-length normalisation (b).",
  lncltc: "SMART lnc.ltc: cosine of log-tf document vectors (no idf) and log-tf·idf query vectors.",
  tf: "Term frequency: occurrences of the term in this chunk's zone.",
  weight: "This term-zone pair's contribution to the score (already multiplied by the zone weight).",
  share: "Share of the total score contributed by this term-zone pair; the rows sum to 100%.",
  stopWords: "Very frequent function words dropped before indexing; they carry almost no meaning.",
  stem: "Porter stemming conflates inflected forms (bonds, bonding → bond) into one index term.",
  position: "Token positions in the chunk body; a phrase matches where the terms sit at consecutive positions.",
  distance: "Cosine distance between query and chunk embeddings — lower means closer in meaning.",
  rerank: "Cross-encoder reranker relevance score — higher is more relevant.",
} as const;
