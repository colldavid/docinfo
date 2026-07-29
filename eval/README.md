# Local Accuracy Evaluation

Measure how well DocInfo's document-type and industry classifiers perform **on your
own documents**, on your own machine.

## Why this exists

DocInfo's published accuracy numbers come from a held-out slice of its training
corpus, and that corpus is synthetic. The fair question is: *does it work on our
real client documents?* This script answers that question with your files.

## The privacy guarantee

**No document content leaves your machine.** This is not a policy promise — it is
how the code works:

- Classification runs on a **sentence-transformers embedding model cached on your
  disk** plus a **scikit-learn logistic regression loaded from a local file**.
- There is **no API key, no LLM call, and no HTTP request** anywhere in this path.
- Only two things are ever written: `eval_report.json` next to your manifest, and
  the summary printed to your terminal.

You can run this with Wi-Fi off. The one exception: the *very first* run on a new
machine needs the embedding model downloaded into its cache. Run the script once
on any throwaway text file while online, and every run after that is fully offline.

## Step 1 — prepare the manifest

Make a CSV listing your documents and their **true** labels (the ground truth, as
a human would label them). Three columns: `path`, `doc_type`, `industry`.

```csv
path,doc_type,industry
contracts/acme_msa.pdf,contract,technology
memos/q3_ops_review.docx,internal_memo,manufacturing
filings/lakeview_10k.pdf,regulatory_filing,healthcare
```

Notes:

- **`path`** is relative to the manifest file's own folder, or an absolute path.
  Keeping the manifest next to your documents is easiest.
- **Leave a label blank to skip it** for that file. If you know a document's
  industry but not its type, fill in `industry` and leave `doc_type` empty — that
  file still counts toward the industry score and is ignored for document type.
- Supported file types: `.pdf`, `.docx`, `.txt`. Anything else is reported as
  unreadable and skipped rather than crashing the run.
- Labels must match the model's vocabulary exactly (e.g. `technology`, not `tech`).
  Any label the model doesn't know is called out as a warning — see below.

Valid `doc_type` values:

```
contract, financial_report, internal_memo, press_release,
regulatory_filing, research_report
```

Valid `industry` values:

```
aerospace_defense, agriculture, automotive, construction, consumer_goods,
consumer_services, defense, education, energy, finance, healthcare,
hospitality, insurance, manufacturing, media, mining, other,
professional_services, real_estate, retail, technology,
telecommunications, transportation, utilities
```

## Step 2 — run it

From the project root:

```bash
PYTHONPATH=. python eval/eval_local.py path/to/manifest.csv
```

Useful flags:

- `-v` — print each file as it is processed (handy for large batches).
- `-o report.json` — write the JSON report somewhere other than the default.

## Step 3 — read the output

The script prints a section per dimension. A typical one:

```
========================================================================
DOCUMENT TYPE
========================================================================
  Accuracy: 80.0%  (4/5 correct)

  Label             Support   Pred    Prec  Recall      F1
  ----------------  -------  -----  ------  ------  ------
  financial_report        1      0    0.00    0.00    0.00
  internal_memo           2      3    0.67    1.00    0.80
```

What the columns mean:

- **Accuracy** — the headline number: share of labeled files the model got right.
- **Support** — how many files you labeled with this class (the ground truth count).
- **Pred** — how many files the model assigned to this class.
- **Precision** — when the model says this label, how often is it right?
  Low precision = over-applying the label.
- **Recall** — of the files that truly are this label, how many did it catch?
  Low recall = missing the label.
- **F1** — the balance of the two. Useful for ranking which classes need work.

With a small sample these per-label numbers swing hard — a class with support of
1 is either 0% or 100%. Treat per-label figures as directional until you have
roughly 20+ examples per class; the overall accuracy stabilizes much sooner.

You also get:

- **Misclassified files** — each wrong prediction with its true label, the
  predicted label, and the model's confidence. Low-confidence mistakes are
  expected; **high-confidence mistakes are the interesting ones** and usually
  point at a label definition that means something different at your firm.
- **Unknown-label warnings** — labels in your manifest that the model was never
  trained on. These can never be scored correctly, so fix them first; they are
  almost always a typo or a naming mismatch.
- **Skipped files** — anything missing, unparseable, or with no extractable text,
  listed with a reason. Scanned PDFs with no text layer show up here, since there
  is no OCR step.

## The JSON report

`eval_report.json` is written next to the manifest and holds everything above
plus a per-file record of every prediction and confidence — useful for charting
results or tracking accuracy across model versions.
