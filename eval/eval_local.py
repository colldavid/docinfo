"""
Offline accuracy evaluation for DocInfo's local classifiers.

Why this exists: the published doc_type / industry accuracy numbers come from a
held-out split of DocInfo's own training corpus. The fair objection is that those
documents are synthetic. This script lets a teammate answer the question on their
own labeled documents.

It can do that because the classifiers are 100% local: sentence-transformers
embeddings from a cached model plus a scikit-learn logistic regression, both
loaded from disk. No document text is sent anywhere. There is no API key, no HTTP
client, and no LLM stage in this path.

Usage:
    PYTHONPATH=. python eval/eval_local.py manifest.csv

Manifest CSV columns: path, doc_type, industry
  - path: relative to the manifest's own directory, or absolute
  - doc_type / industry: the TRUE label. Leave either blank to skip that
    dimension for that file (e.g. you know the industry but not the type).

Writes eval_report.json next to the manifest and prints a summary to stdout.
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Allow `python eval/eval_local.py` to work without PYTHONPATH by putting the
# project root on sys.path. Harmless when PYTHONPATH=. is already set.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.ingestion import parse_document  # noqa: E402
from app.pipelines.classifier import (  # noqa: E402
    classify_document_type,
    classify_industry,
    get_doc_type_classifier,
    get_industry_classifier,
)

PRIVACY_NOTE = (
    "Running fully offline - no document content leaves this machine.\n"
    "(First-ever run needs the embedding model cached; run once on any text "
    "file while online.)"
)

DIMENSIONS = ("doc_type", "industry")


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

def load_manifest(manifest_path: Path) -> list[dict]:
    """
    Read the manifest CSV into rows of {path, doc_type, industry}.

    Relative paths resolve against the manifest's directory, which is what a user
    expects when the manifest sits alongside the documents.
    """
    base_dir = manifest_path.parent
    rows = []

    with manifest_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "path" not in {
            (name or "").strip().lower() for name in reader.fieldnames
        }:
            raise ValueError(
                f"Manifest {manifest_path} must have a 'path' column. "
                f"Found: {reader.fieldnames}"
            )

        for line_no, raw in enumerate(reader, start=2):
            # Normalize header casing/whitespace so 'Path' or ' doc_type ' work.
            row = {
                (key or "").strip().lower(): (value or "").strip()
                for key, value in raw.items()
                if key is not None
            }
            raw_path = row.get("path", "")
            if not raw_path:
                continue  # Skip blank lines silently.

            candidate = Path(raw_path)
            resolved = candidate if candidate.is_absolute() else (base_dir / candidate)

            rows.append({
                "line": line_no,
                "raw_path": raw_path,
                "path": resolved,
                "doc_type": row.get("doc_type", ""),
                "industry": row.get("industry", ""),
            })

    return rows


def check_unknown_labels(rows: list[dict]) -> dict[str, list[str]]:
    """
    Find manifest labels that are not in the trained encoders' vocabularies.

    A label the model was never trained on can never be predicted, so it would
    silently drag accuracy down. Surfacing it usually means a typo or a naming
    mismatch (e.g. 'tech' vs 'technology').
    """
    vocab = {
        "doc_type": set(get_doc_type_classifier().encoder.classes_),
        "industry": set(get_industry_classifier().encoder.classes_),
    }

    unknown: dict[str, list[str]] = {}
    for dimension in DIMENSIONS:
        used = {row[dimension] for row in rows if row[dimension]}
        missing = sorted(used - vocab[dimension])
        if missing:
            unknown[dimension] = missing
    return unknown


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(pairs: list[tuple[str, str]]) -> dict:
    """
    Per-label precision / recall / F1 plus overall accuracy, from (true, pred) pairs.

    Computed with collections rather than sklearn so the numbers stay easy to
    audit by hand — precision = TP/(TP+FP), recall = TP/(TP+FN).
    """
    if not pairs:
        return {"n": 0, "accuracy": None, "per_label": {}}

    correct = sum(1 for true, pred in pairs if true == pred)
    true_counts = Counter(true for true, _ in pairs)
    pred_counts = Counter(pred for _, pred in pairs)
    hits = Counter(true for true, pred in pairs if true == pred)

    per_label = {}
    for label in sorted(set(true_counts) | set(pred_counts)):
        tp = hits[label]
        precision = tp / pred_counts[label] if pred_counts[label] else 0.0
        recall = tp / true_counts[label] if true_counts[label] else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )
        per_label[label] = {
            "support": true_counts[label],
            "predicted": pred_counts[label],
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    return {
        "n": len(pairs),
        "correct": correct,
        "accuracy": round(correct / len(pairs), 4),
        "per_label": per_label,
    }


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(rows: list[dict], verbose: bool = False) -> dict:
    """Classify every manifest row and collect predictions, errors, and skips."""
    results = []
    unreadable = []
    pairs: dict[str, list[tuple[str, str]]] = {dim: [] for dim in DIMENSIONS}
    misclassified: dict[str, list[dict]] = {dim: [] for dim in DIMENSIONS}

    for index, row in enumerate(rows, start=1):
        path = row["path"]
        display = row["raw_path"]

        if verbose:
            print(f"  [{index}/{len(rows)}] {display}", flush=True)

        if not path.exists():
            unreadable.append({"path": display, "reason": "file not found"})
            continue

        try:
            text = parse_document(path)
        except Exception as exc:  # parse_document catches most, belt and braces
            unreadable.append({"path": display, "reason": f"parse error: {exc}"})
            continue

        if not text or not text.strip():
            unreadable.append({"path": display, "reason": "no extractable text"})
            continue

        try:
            doc_type_pred, doc_type_conf = classify_document_type(text)
            industry_labels, industry_scores = classify_industry(text)
            # classify_industry is multi-label; the top entry is the prediction.
            industry_pred = industry_labels[0] if industry_labels else ""
            industry_conf = industry_scores[0] if industry_scores else 0.0
        except Exception as exc:
            unreadable.append({"path": display, "reason": f"classification error: {exc}"})
            continue

        entry = {
            "path": display,
            "chars": len(text),
            "doc_type": {
                "true": row["doc_type"] or None,
                "predicted": doc_type_pred,
                "confidence": round(float(doc_type_conf), 4),
            },
            "industry": {
                "true": row["industry"] or None,
                "predicted": industry_pred,
                "confidence": round(float(industry_conf), 4),
            },
        }
        results.append(entry)

        for dimension in DIMENSIONS:
            true_label = row[dimension]
            if not true_label:
                continue  # Blank cell -> this dimension is not evaluated for this file.
            predicted = entry[dimension]["predicted"]
            pairs[dimension].append((true_label, predicted))
            if true_label != predicted:
                misclassified[dimension].append({
                    "path": display,
                    "true": true_label,
                    "predicted": predicted,
                    "confidence": entry[dimension]["confidence"],
                })

    return {
        "results": results,
        "unreadable": unreadable,
        "metrics": {dim: compute_metrics(pairs[dim]) for dim in DIMENSIONS},
        "misclassified": misclassified,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_metrics(dimension: str, metrics: dict, misclassified: list[dict]) -> None:
    title = "DOCUMENT TYPE" if dimension == "doc_type" else "INDUSTRY"
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)

    if not metrics["n"]:
        print("  No labeled examples in the manifest — skipped.")
        return

    accuracy = metrics["accuracy"]
    print(
        f"  Accuracy: {accuracy:.1%}  "
        f"({metrics['correct']}/{metrics['n']} correct)"
    )
    print()

    label_width = max(len(label) for label in metrics["per_label"])
    label_width = max(label_width, 5)
    print(
        f"  {'Label'.ljust(label_width)}  {'Support':>7}  {'Pred':>5}  "
        f"{'Prec':>6}  {'Recall':>6}  {'F1':>6}"
    )
    print(f"  {'-' * label_width}  {'-' * 7}  {'-' * 5}  {'-' * 6}  {'-' * 6}  {'-' * 6}")
    for label, stats in metrics["per_label"].items():
        print(
            f"  {label.ljust(label_width)}  {stats['support']:>7}  "
            f"{stats['predicted']:>5}  {stats['precision']:>6.2f}  "
            f"{stats['recall']:>6.2f}  {stats['f1']:>6.2f}"
        )

    print()
    if misclassified:
        print(f"  Misclassified ({len(misclassified)}):")
        for item in misclassified:
            print(
                f"    {item['path']}\n"
                f"      true: {item['true']}  ->  predicted: {item['predicted']} "
                f"(confidence {item['confidence']:.0%})"
            )
    else:
        print("  Misclassified: none")


def print_summary(report: dict) -> None:
    print()
    print("=" * 72)
    print("DOCINFO LOCAL EVALUATION")
    print("=" * 72)
    print(f"  Manifest:   {report['manifest']}")
    print(f"  Files listed: {report['files_listed']}")
    print(f"  Files scored: {report['files_scored']}")

    unknown = report.get("unknown_labels") or {}
    if unknown:
        print()
        print("  WARNING - labels not in the trained model's vocabulary:")
        for dimension, labels in unknown.items():
            print(f"    {dimension}: {', '.join(labels)}")
        print("    These can never be predicted correctly. Check for typos.")

    unreadable = report.get("unreadable") or []
    if unreadable:
        print()
        print(f"  Skipped {len(unreadable)} unreadable file(s):")
        for item in unreadable:
            print(f"    {item['path']} - {item['reason']}")

    for dimension in DIMENSIONS:
        _print_metrics(
            dimension,
            report["metrics"][dimension],
            report["misclassified"][dimension],
        )

    print()
    print("=" * 72)
    print(f"  Full results written to: {report['report_path']}")
    print("=" * 72)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate DocInfo's local doc_type / industry classifiers against "
            "your own labeled documents. Runs fully offline."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Manifest CSV columns: path, doc_type, industry\n"
            "  path      relative to the manifest's directory, or absolute\n"
            "  doc_type  true label, or blank to skip this dimension\n"
            "  industry  true label, or blank to skip this dimension\n"
        ),
    )
    parser.add_argument("manifest", type=Path, help="Path to the manifest CSV.")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Where to write the JSON report (default: eval_report.json next to the manifest).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print each file as it is processed.",
    )
    args = parser.parse_args(argv)

    print(PRIVACY_NOTE)
    print()

    manifest_path = args.manifest.expanduser().resolve()
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    try:
        rows = load_manifest(manifest_path)
    except (ValueError, OSError) as exc:
        print(f"ERROR: could not read manifest: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print(f"ERROR: manifest {manifest_path} contains no rows.", file=sys.stderr)
        return 1

    print(f"Loaded {len(rows)} file(s) from {manifest_path.name}.")
    print("Loading local models (first call may take a moment)...", flush=True)

    try:
        unknown = check_unknown_labels(rows)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("Classifying...", flush=True)
    outcome = evaluate(rows, verbose=args.verbose)

    output_path = args.output or (manifest_path.parent / "eval_report.json")
    report = {
        "manifest": str(manifest_path),
        "report_path": str(output_path),
        "files_listed": len(rows),
        "files_scored": len(outcome["results"]),
        "unknown_labels": unknown,
        "unreadable": outcome["unreadable"],
        "metrics": outcome["metrics"],
        "misclassified": outcome["misclassified"],
        "per_file": outcome["results"],
    }

    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
