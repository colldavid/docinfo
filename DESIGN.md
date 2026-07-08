# DocInfo — Design Decisions & Classification Architecture

A running record of what we built, why, and how quality is measured.
Add to this when new decisions are made.

---

## Classification dimensions

### 1. Document Type
**Technique:** Embeddings + logistic regression trained on SEC EDGAR filings.
**Why:** Fully deterministic, no API cost per call, auditable. EDGAR provides thousands of
labeled examples with known filing types (10-K, 10-Q, 8-K, etc.) mapped to our taxonomy.
**Output:** `label` + `probability` — a real calibrated probability from the classifier,
not an LLM self-report.
**Ground truth:** EDGAR filing type metadata (objective).
**Eval metric:** Accuracy + per-label precision/recall against held-out EDGAR examples.

### 2. Industry / Sector
**Technique:** Same as document type — embeddings + logistic regression.
**Ground truth:** SEC SIC codes mapped to our taxonomy via `data/sic_to_industry.json`
(editable file — adjust mappings without retraining).
**Output:** `labels` + `probabilities` — multi-label, returns all industries above a
probability threshold. A document can span multiple sectors.
**Eval metric:** Top-k accuracy (ground truth label appears anywhere in predicted list).

### 3. Pain Points
**Technique:** Two-step — LLM candidate generation + embedding similarity matching.
1. Haiku generates ~25 industry-specific pain point candidates (e.g. for healthcare:
   "reimbursement rate compression", "FDA approval delays").
2. Both the candidates and the document are embedded. Cosine similarity is computed
   between the document embedding and each candidate embedding.
3. Candidates above a tunable similarity threshold (default 0.65) are returned.
**Why this split:** LLM handles contextual vocabulary generation per industry;
embeddings handle matching. Clean separation of responsibilities.
**Caching:** Candidates are cached per industry in `cache/pain_points_cache.json`.
First call per industry hits Haiku; every subsequent run uses the cache.
**Output:** `label` + `similarity_score` — cosine similarity, not a probability.
**Ground truth:** None. Pain points are inherently subjective.
**Eval:** Threshold is a sensitivity dial, not a calibrated metric. Spot-check by
reviewing whether surfaced matches feel relevant on real documents. A lower threshold
surfaces more matches (noisier); higher is more precise (may miss things). Tune via
`--mode threshold-calibration` once you have a hand-labeled spot-check set.

### 4. Confidentiality
**Technique:** LLM call (Haiku) with a locked rubric + few-shot examples.
**Levels:** public → internal → confidential → restricted (ordered by sensitivity).
**Why LLM:** Confidentiality is a relational judgment — context-dependent, not a
semantic property of the text alone. A rubric with examples anchors the model's
interpretation consistently.
**Output:** `label` + `confidence` (LLM self-report, 0–1) + `needs_review` (bool).
**Consistency check:** If confidence < 0.7, re-runs N times at temp 0.4. If runs
disagree, sets `needs_review: true`. Triggered automatically, cost-controlled.
**Ground truth:** Sonnet-as-judge on a sample set (see Quality section below).
Current eval set labels are Sonnet-generated; should be replaced with human-validated
labels when the team has bandwidth.
**Eval metric:** Agreement rate vs. Sonnet-as-judge labels. Reflects inter-rater
agreement, not objective accuracy — confidentiality is inherently context-dependent.
**Ablation:** Write rubric variants as `.txt` files, run
`--mode ablation-confidentiality` to compare agreement rates across variants.

### 5. Importance Level
**Technique:** LLM call (Haiku) with a locked rubric + few-shot examples.
**Explicit inputs passed to the prompt:** detected pain points + confidentiality label
(from steps 3 and 4) + document text (first 3000 chars).
**Rubric:**
- High: actionable pain points AND (material financial content OR confidential/restricted)
- Medium: some pain points OR confidential/restricted content, but not both together
- Low: no pain points AND public/internal confidentiality
**Why pain points and confidentiality are passed explicitly:** Without structured inputs,
two documents with identical text but different detected pain points would get the same
importance score. Passing them as inputs makes the dependency auditable and consistent.
**Output:** `label` + `confidence` (LLM self-report) + `rationale` + `needs_review`.
**Consistency check:** Same as confidentiality — triggers on confidence < 0.7.
**Ground truth:** Sonnet-as-judge (same notes as confidentiality above).
**Eval metric:** Agreement rate vs. Sonnet-as-judge labels.
**Ablation:** `--mode ablation` compares rubric variants by agreement rate.

---

## Quality measurement & calibration

### Sonnet-as-judge
**What:** Send documents to Sonnet (stronger, more expensive model) with a neutral
prompt — not the Haiku rubric — and use its answers as ground truth labels for
confidentiality and importance.
**Why:** Avoids the circular problem where eval labels are written by the same person
who wrote the rubric, making agreement trivially high. Sonnet as an independent judge
breaks that circularity.
**How:** Run `eval/generate_labels.py` (to be built) on the eval set. Labels are
written back to `eval/labeled_set.jsonl`. Current labels are Sonnet-generated.
**Limitation:** Sonnet is still an LLM — not a human. Labels should eventually be
validated against real human reviewer judgments, but Sonnet is a good automated proxy
for iterative development.

### Self-consistency entropy
**What:** Run each document through the importance/confidentiality pipeline N times at
higher temperature (temp 0.4). Compute entropy of the resulting label distribution.
**Why:** High entropy = model is genuinely uncertain about this document.
Low entropy = model is confident. A cheap automated uncertainty signal that requires
no ground truth.
**Current implementation:** The consistency check already does this partially —
it re-runs and flags disagreement as `needs_review: true`. Could be extended to
report entropy as a continuous signal rather than a binary flag.
**Use case:** Surface genuinely ambiguous documents for human review rather than
spot-checking randomly.

### KL divergence from Sonnet's distribution
**What:** Treat Sonnet's label distribution over a large sample (e.g. 500 documents)
as reference distribution P; Haiku's distribution as Q. Compute KL(P‖Q).
**Why:** Detects rubric drift when you change a prompt. If the label distribution
shifts significantly from the Sonnet reference even when individual agreement rates
look fine, something changed materially.
**Use case:** Run after any rubric change as a sanity check that the overall
distribution hasn't shifted unexpectedly.
**Status:** Not yet implemented. Add to eval harness when the labeled set is large
enough to estimate distributions reliably (roughly 200+ documents per dimension).

### Embedding distance from training examples
**What:** For document type and industry (classifier dimensions), compute each
document's distance in embedding space from its nearest neighbor in the training set.
**Why:** Documents far from anything seen during training are likely to be
misclassified. This gives a coverage signal without needing ground truth labels.
**Use case:** Flag out-of-distribution documents — e.g. a document type that didn't
exist in the EDGAR training data. Useful when expanding to new document categories.
**Status:** Not yet implemented.

### Eval harness modes
| Mode | What it does |
|---|---|
| `full` | Runs all dimension evals, prints summary report |
| `ablation --dimension importance` | Compares importance rubric variants by agreement rate vs. Sonnet labels |
| `ablation --dimension confidentiality` | Same for confidentiality rubric variants |

Run from project root: `PYTHONPATH=. python eval/harness.py --data eval/labeled_set.jsonl`

---

## Key design principles

**Structured inputs over prompt stuffing.** Pain points and confidentiality are passed
as explicit fields to the importance prompt, not buried in the document text. This makes
the pipeline's reasoning auditable and ensures upstream outputs actually influence
downstream decisions.

**Separate technique per dimension.** Each dimension uses the technique best suited
to it: deterministic classifiers where ground truth exists (document type, industry);
LLM with locked rubric where judgment is relational (confidentiality, importance);
hybrid LLM + embeddings where vocabulary is dynamic but matching is structural
(pain points).

**Named fields reflect what they actually measure.** `probability` = calibrated
classifier output. `confidence` = LLM self-report. `similarity_score` = cosine
similarity. These are intentionally different names because they measure different
things.

**Minimize team dependency for quality checks.** The goal is to catch quality issues
automatically (self-consistency entropy, KL divergence, embedding distance) so the
team only reviews genuinely ambiguous documents (`needs_review: true`) rather than
doing routine spot-checks.
