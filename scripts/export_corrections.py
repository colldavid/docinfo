"""
Export human label corrections as classifier training data.

Consultants correct wrong doc_type / industry labels in the UI (see
app/routes/corrections.py). This script turns those corrections into JSONL
training records so the next `train.py` run learns the firm's own document
universe.

Usage:
    PYTHONPATH=. python scripts/export_corrections.py

Writes (append-only):
    data/corrections_doc_type.jsonl
    data/corrections_industry.jsonl
    data/corrections_exported.json   — ids already exported, for dedupe

Re-running is safe: each record id is exported at most once per dimension, so
the JSONL files never accumulate duplicates of the same document.

Record format matches data/edgar/labeled_samples.jsonl, which train.py's
load_samples() reads:
    {"text": ..., "doc_type_label": ..., "industry_label": ...}
Note load_samples() SKIPS any row without a truthy "doc_type_label", so the
industry file also carries a doc_type_label whenever one is known (falling back
to the model's prediction) — otherwise those rows would be silently dropped.
"""
import json
import sys
from pathlib import Path

# Text truncation used when the EDGAR training set was built
# (data/edgar_download.py:233 -> text[start:start + 8000]). Matching it keeps
# corrections distributionally consistent with the rest of the training data.
MAX_TEXT_CHARS = 8000

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _PROJECT_ROOT / "data"
DOC_TYPE_FILE = DATA_DIR / "corrections_doc_type.jsonl"
INDUSTRY_FILE = DATA_DIR / "corrections_industry.jsonl"
EXPORTED_FILE = DATA_DIR / "corrections_exported.json"


def load_exported() -> dict[str, set[int]]:
    """Read the dedupe ledger of already-exported record ids."""
    if not EXPORTED_FILE.exists():
        return {"doc_type": set(), "industry": set()}
    try:
        raw = json.loads(EXPORTED_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: could not read {EXPORTED_FILE.name} ({e}); treating as empty.")
        print("         This may re-export corrections that were already exported.")
        return {"doc_type": set(), "industry": set()}
    return {
        "doc_type": set(raw.get("doc_type") or []),
        "industry": set(raw.get("industry") or []),
    }


def save_exported(exported: dict[str, set[int]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTED_FILE.write_text(
        json.dumps(
            {
                "doc_type": sorted(exported["doc_type"]),
                "industry": sorted(exported["industry"]),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def append_records(path: Path, records: list[dict]) -> None:
    """Append JSONL records. No-op when there is nothing new."""
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    # Imported here (not at module scope) so --help-style usage errors surface
    # before we pay the cost of spinning up the DB engine.
    from sqlalchemy import or_

    from app.database import ClassificationRecord, get_session, init_db

    init_db()  # ensure the user_doc_type / user_industry columns exist

    exported = load_exported()

    with get_session() as session:
        records = (
            session.query(ClassificationRecord)
            .filter(
                or_(
                    ClassificationRecord.user_doc_type.isnot(None),
                    ClassificationRecord.user_industry.isnot(None),
                )
            )
            .filter(ClassificationRecord.doc_text.isnot(None))
            .order_by(ClassificationRecord.id)
            .all()
        )

        doc_type_new: list[dict] = []
        industry_new: list[dict] = []
        skipped_empty = 0

        for r in records:
            text = (r.doc_text or "").strip()
            if not text:
                skipped_empty += 1
                continue
            text = text[:MAX_TEXT_CHARS]

            if r.user_doc_type and r.id not in exported["doc_type"]:
                doc_type_new.append({
                    "text": text,
                    "doc_type_label": r.user_doc_type,
                    # Best known industry, so this row is also usable by the
                    # industry classifier rather than defaulting to "other".
                    "industry_label": r.user_industry or r.industry or "other",
                    "source": "correction",
                    "record_id": r.id,
                })
                exported["doc_type"].add(r.id)

            if r.user_industry and r.id not in exported["industry"]:
                # load_samples() drops rows with no doc_type_label, so fall back
                # to the model's prediction to keep this row usable.
                doc_type_label = r.user_doc_type or r.doc_type_label
                if not doc_type_label:
                    print(
                        f"  note: record {r.id} has an industry correction but no doc type at all; "
                        "train.py's load_samples() would skip it. Exporting anyway."
                    )
                industry_new.append({
                    "text": text,
                    "doc_type_label": doc_type_label,
                    "industry_label": r.user_industry,
                    "source": "correction",
                    "record_id": r.id,
                })
                exported["industry"].add(r.id)

    append_records(DOC_TYPE_FILE, doc_type_new)
    append_records(INDUSTRY_FILE, industry_new)
    save_exported(exported)

    # ---- Summary ----------------------------------------------------------
    print()
    print(f"{len(doc_type_new)} new doc_type corrections")
    print(f"{len(industry_new)} new industry corrections")
    if skipped_empty:
        print(f"({skipped_empty} corrected record(s) skipped — empty doc_text)")

    if not doc_type_new and not industry_new:
        print()
        print("Nothing new to export. Corrections already exported are tracked in")
        print(f"  {EXPORTED_FILE.relative_to(_PROJECT_ROOT)}")
        return 0

    print()
    print("Wrote:")
    if doc_type_new:
        print(f"  {DOC_TYPE_FILE.relative_to(_PROJECT_ROOT)}")
    if industry_new:
        print(f"  {INDUSTRY_FILE.relative_to(_PROJECT_ROOT)}")

    # train.py takes a SINGLE --data file and does not glob data/ for extra
    # JSONL, so these files are NOT picked up automatically. Concatenating into
    # a combined file is the no-code-change path.
    print()
    print("Next step — add these files to your train.py data sources and run:")
    print("  PYTHONPATH=. python train.py")
    print()
    print("NOTE: train.py currently reads exactly one --data file and does not")
    print("auto-discover data/corrections_*.jsonl. Until it supports multiple")
    print("inputs, combine them first:")
    print()
    print("  cat data/edgar/labeled_samples.jsonl data/corrections_*.jsonl \\")
    print("    > data/combined_training.jsonl")
    print("  PYTHONPATH=. python train.py --data data/combined_training.jsonl")

    return 0


if __name__ == "__main__":
    sys.exit(main())
