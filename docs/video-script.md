# Keystone demo video script (two presenters, about 7½ minutes)

Every expected result below was checked against the live app on 2026-10-07 (fresh account,
Chapter 1 + Chapter 2 uploaded). If the screen shows the same thing, you're on track.

Speakers are **A** and **B**. Lines say "the system" / "Keystone" / "we", never "I built" or "my part".

---

## Before you press record (10 minutes)

1. **Start everything** (see README "Run"): Docker Desktop running, then
   `docker compose up -d postgres redis`, the API (`uvicorn main:app --host 127.0.0.1 --port 8010`),
   the **worker** (`arq worker.WorkerSettings`) and the frontend (`npm run dev`).
2. Open http://localhost:5173 and **refresh** (the tab should say *Keystone*).
3. **Sign up with a new account** (an empty org, so the upload is processed live and there is no old chat history).
4. In **Notebooks**, create a notebook named **Climate textbook**.
5. Have `backend/eval/data/textbook/chapter1.pdf` and `chapter2.pdf` ready in a file browser.
6. Open VS Code next to the browser with these files in tabs:
   `backend/app/services/retrieval/sparse/scoring.py`, `backend/app/services/retrieval/fusion.py`,
   `backend/app/services/chat/citation_check.py`, `docs/report/report.pdf` (Section 5).
7. Close other apps and notifications; browser zoom 110–125% so text is readable.
8. Do one quick test question in a *different* notebook to confirm the AI key works.

**Order matters.** Section summaries (used for "summarize" questions) are ready about 1½ minutes
after upload, so the script does Search first and the summary question last.

---

## 1. The problem and the track (0:00–0:45), Speaker A
*Screen: Keystone home page.*

> "This is Keystone, a question-answering system over your own documents, built for Track T1,
> Retrieval-Augmented Generation and trustworthy answers.
> AI chat tools answer questions about documents fluently, but you can't tell which parts come from
> the sources and which are invented. The track's premise is that a language model is only as good
> as what gets retrieved for it.
> So Keystone does three things: it retrieves with a search engine you can inspect, it links every
> sentence of an answer to a ranked passage and page, and it re-checks each sentence against the
> passage it cites."

## 2. Upload and processing (0:45–1:35), Speaker B
*Screen: Repository → upload chapter1.pdf and chapter2.pdf; Status column goes UPLOADED → … → READY
(about 25–30 s). Then open the Climate textbook notebook and add both documents from the left panel.*

> "A background worker processes each PDF in stages. It reads the PDF's own text, page by page, so
> every passage keeps its page number. Then it decides what a 'document' means for retrieval: chunks
> of about a thousand characters that never cross a section boundary. Small enough to cite exactly,
> big enough to hold a full definition.
> Each chunk is indexed twice: as an embedding for meaning-based search, and in an inverted index
> written from scratch. Both chapters are ready in under half a minute."

## 3. The search engine, opened up (1:35–3:35), Speaker A
*Screen: **Search** in the sidebar, notebook Climate textbook.*

**3a. Ranked tab**, scheme **BM25**, query `Ekman transport`, Search.
> "Every query goes through the same steps as the documents: tokenizing, case folding, stop-word
> removal and Porter stemming. All four steps are shown here. The table shows each term's document
> frequency and inverse document frequency: 'ekman' is rare, so it weighs much more than 'transport'."

Click **Why this score?** on the first result (section *1.3.2 Oceanic circulation*, pp. 9–10).
> "Each result shows exactly how its score was built: term frequency, idf and weight per term."

**3b.** Switch to **tf-idf (lnc.ltc)**, same query.
*Expected: the top result becomes "2.1.5.2 Heat transport" (p. 17), which doesn't mention Ekman.*
> "With classic tf-idf cosine scoring, a short heading like 'Heat transport' jumps to first place:
> cosine normalisation inflates terms in very short headings. BM25 normalises by average length and
> caps term frequency, so it keeps the Ekman passage first. That's why production uses BM25."

**3c. Boolean tab**: `ekman AND upwelling` (→ **2 matches**), then `ekman AND NOT upwelling` (→ **1 match**).
> "Boolean retrieval merges sorted postings lists, starting with the rarest term. Two plus one: exactly
> the three passages that contain 'ekman'."

**3d. Phrase tab**: `ekman transport` (→ **3 matches**).
> "Phrase search uses the positional index: both words must sit next to each other."

**3e. Semantic (hybrid) tab**: `what is ekman transport`.
*Expected: #1 pp. 9–10 (dense #1, BM25 #1); #3 pp. 10–11 has dense #5, BM25 #2; #6 has dense #3, BM25 #11.*
> "Production search combines two ranked lists: embedding similarity and BM25. Reciprocal Rank Fusion
> merges them by rank. This one was only fifth by meaning but second by BM25, so it ends up third,
> above this one, which was third by meaning but eleventh by BM25. Each card shows the fused score and
> both ranks, so the order is explainable."

## 4. Asking questions (3:35–5:00), Speaker B
*Screen: Notebooks → Climate textbook → **Chat** tab.*

1. `what is ekamn transport` (typo on purpose).
   *Expected: answer about wind-driven transport perpendicular to the wind stress, citation on p. 9,
   claim check "2 supported".*
   > "Even with a typo, it finds the right passage. Each sentence ends with a citation number."
2. Click the citation **[1]**.
   > "Clicking it opens the exact passage, page 9 of Chapter 1."
3. Point at the claim-check line under the answer.
   > "Each sentence is checked against the passage it cites and marked supported, weak or uncited."
4. Click **Debug** under the answer.
   > "The debug view shows the exact chunks the model saw, with their fused score and ranks, and the
   > full prompt. Nothing is hidden."
5. `Who won the 2022 FIFA World Cup?`
   *Expected: "I don't have that in the provided sources."*
   > "When the documents don't contain the answer, it refuses instead of guessing."
6. `Summarize the key points of these chapters`
   *Expected: a structured summary with many section citations (takes ~10–15 s).*
   > "Broad questions can't be answered from eight passages. A small classifier recognises them and
   > combines summaries of every section instead."

## 5. The code behind it (5:00–5:45), Speaker A
*Screen: VS Code.*

1. `scoring.py` → function `search`.
   > "Scoring runs term by term, adding each term's contribution into a running total per chunk, and a
   > heap picks the top results. Here is the BM25 formula, and here the tf-idf cosine version."
2. Same file → `intersect`.
   > "Boolean AND is a linear merge of two sorted postings lists."
3. `fusion.py` → `fuse_rrf`.
   > "Fusion adds one over sixty-plus-rank from each list."
4. `citation_check.py` → `score_claims`.
   > "The checker turns each answer sentence into a tf-idf vector, compares it by cosine with the best
   > one- or two-sentence window of the cited passage, and combines that with embedding similarity."

## 6. Evaluation (5:45–6:50), Speaker B
*Screen: report.pdf, Section 5 (tables and the two charts).*

> "The retriever was evaluated two ways.
> First, the public SciFact benchmark: about five thousand abstracts and three hundred judged queries.
> nDCG at 10 goes from 0.619 for the tf-idf baseline to 0.686 for BM25, 0.717 for embeddings and 0.735
> for the hybrid. Champion lists keep the same quality 2.4 times faster.
> Second, eighteen questions on these two chapters, judged page by page. The hybrid finds a relevant
> page in the top five for every question, with a mean reciprocal rank of 0.935.
> This chart is a finding from testing: the hosted PDF parser used first silently dropped bold text,
> which in a textbook is the key terms. Reading the PDF's own text layer improved every method; BM25
> went from 0.798 to 0.909.
> The citation checker was also tested on SciFact. It almost never accepts a citation to an unrelated
> passage, but it measures topic, not truth: most sentences that contradict their source still pass."

## 7. Limitation, live, and next steps (6:50–7:30), Speaker A
*Screen: Search → Ranked → BM25 → `ekamn transport`.*
*Expected: the first Ekman passage only appears around rank 9.*

> "One limitation, live: the BM25 index has no spelling correction, so the typo only matches
> 'transport' and the right passage drops to ninth. Embedding search covers this today; spelling
> correction with k-gram indexes is the next step.
> Other limits: 'supported' means on-topic, not proven true, so a contradiction check is planned;
> section summaries arrive about a minute after upload; and answers take five to fifteen seconds.
> Thanks for watching."

*(Optional, 15 s, if you want to cover the guideline that members explain their own components:
a closing line naming who owned which component, from the report's work-division table.)*

---

## Question bank (all verified; use these if you want variety)

**Chat questions that answer well (with expected citation):**

| Question | Expected |
|---|---|
| `what is ekamn transport` | Ekman transport, p. 9, all claims supported |
| `What drives the thermohaline circulation?` | temperature + salinity (density) contrasts, pp. 10–11 |
| `What happens to the salt in seawater when sea ice forms?` | brine rejection, p. 20 |
| `What causes monsoons?` | land–sea heating contrast and wind reversal, p. 7 |
| `What is the biological pump in the ocean?` | biological pumps / soft tissue pump, pp. 28–29 |
| `Why do snow and ice have a high albedo?` | they reflect most incoming solar radiation, p. 20 |
| `What is the Earth's energy budget?` | incoming solar vs outgoing radiation, several pages |

**Questions that should be refused** ("I don't have that in the provided sources."):
`Who won the 2022 FIFA World Cup?`, `What is the capital of France?`, `Who wrote Hamlet?`,
`How do I bake a chocolate cake?`

**Broad questions** (wait ~1½ min after upload): `Summarize the key points of these chapters`,
`Give me an overview of chapter 2`. The **Overview** tab next to Chat also generates a notebook summary.

**Search page queries:**

| Tab | Query | Expected |
|---|---|---|
| Ranked, BM25 | `Ekman transport` | top: 1.3.2 Oceanic circulation, pp. 9–10 |
| Ranked, tf-idf | `Ekman transport` | top: 2.1.5.2 Heat transport, p. 17 (the tf-idf weakness) |
| Ranked, BM25 | `ekamn transport` | Ekman passage only ~rank 9 (limitation) |
| Ranked | `Ekman transport` + tick **Champion lists** | same top results; on these 2 chapters the candidate set stays 26, because champion lists (top 50 postings per term) only prune frequent terms in large corpora. The speed-up is the SciFact result (2.4×), so say that rather than demo it |
| Boolean | `ekman` / `ekman AND upwelling` / `ekman AND NOT upwelling` | 3 / 2 / 1 |
| Boolean | `monsoon OR hadley` | 9 |
| Boolean | `sea AND ice AND salinity` | 3 |
| Phrase | `ekman transport` / `brine rejection` / `thermohaline circulation` / `greenhouse effect` | 3 / 2 / 5 / 5 |
| Semantic (hybrid) | `what is ekman transport` | #1 pp. 9–10, dense #1 + BM25 #1 |

Phrase and Boolean want **search terms**, not full questions: a question like "which element is most
common in the human body" returns 0 there because words like "elem" are not in the index.

---

## If something goes wrong while recording

| What you see | Fix |
|---|---|
| Document stuck at UPLOADED / PARSING | the worker isn't running: start `arq worker.WorkerSettings` |
| "The AI model provider request failed…" | OpenRouter key/credits: fix `OPENAI_API_KEY` in `backend/.env`, restart the API |
| "Summarize…" gives a normal short answer instead of a structured summary | summaries not ready yet: wait a minute and ask again |
| Answer differs slightly from this script | expected: wording varies; the cited page and the refusal behaviour are what matter |
| Upload says the file already exists | the same PDF is already in this org: use a fresh account |
| Tab still says the old name | hard-refresh the browser (Ctrl+F5) |

Checklist from the assignment, all covered above: problem + track (§1), end to end on real inputs
(§2–4), at least one limitation (§7), pipeline with real intermediate output and code (§3, §5),
evaluation against a baseline (§6), 5–8 minutes, no slides, upload as **unlisted**.
