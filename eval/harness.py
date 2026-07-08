"""
Eval harness — runs the full pipeline on a labeled evaluation set and reports
per-dimension metrics.

Usage:
    # Full eval against labeled set
    PYTHONPATH=. python eval/harness.py --data eval/labeled_set.jsonl

    # Pain point threshold calibration sweep
    PYTHONPATH=. python eval/harness.py --data eval/labeled_set.jsonl --mode threshold-calibration

    # Importance prompt ablation: compare rubric variants
    PYTHONPATH=. python eval/harness.py --data eval/labeled_set.jsonl --mode ablation \
        --rubric-variants eval/rubric_v1.txt eval/rubric_v2.txt

    # Confidentiality prompt ablation
    PYTHONPATH=. python eval/harness.py --data eval/labeled_set.jsonl --mode ablation-confidentiality \
        --rubric-variants eval/conf_rubric_v1.txt eval/conf_rubric_v2.txt

Labeled set format (JSONL, one record per line):
{
  "filepath": "path/to/doc.pdf",                 # used to load text from disk
  "doc_type_label": "financial_report",           # ground truth (EDGAR-derived)
  "industry_label": "technology",                 # ground truth (SIC-derived)
  "pain_points": ["supply chain delay"],          # hand-labeled spot-check (optional)
  "confidentiality_label": "confidential",        # Sonnet-as-judge or hand-labeled
  "importance_label": "high"                      # Sonnet-as-judge or hand-labeled
}

Output: console report + eval/report_<timestamp>.json
"""

import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from sklearn.metrics import classification_report, accuracy_score, precision_recall_fscore_support

from app.ingestion import parse_document
from app.pipelines.classifier import classify_document_type, classify_industry
from app.pipelines.pain_points import detect_pain_points
from app.pipelines.confidentiality import classify_confidentiality
import app.pipelines.confidentiality as conf_module
from app.pipelines.importance import classify_importance
import app.pipelines.importance as imp_module

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = typer.Typer()

EVAL_OUTPUT_DIR = Path("eval")


# ---------------------------------------------------------------------------
# Labeled set loading
# ---------------------------------------------------------------------------

def load_labeled_set(path: Path) -> list[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_text_for_record(record: dict) -> str | None:
    fp = Path(record["filepath"])
    if not fp.exists():
        logger.warning(f"File not found, skipping: {fp}")
        return None
    return parse_document(fp)


# ---------------------------------------------------------------------------
# Per-dimension eval functions
# ---------------------------------------------------------------------------

def eval_doc_type(records: list[dict]) -> dict:
    """Accuracy + per-label precision/recall for document_type."""
    y_true, y_pred = [], []
    for r in records:
        if "doc_type_label" not in r:
            continue
        text = load_text_for_record(r)
        if not text:
            continue
        pred_label, _ = classify_document_type(text)
        y_true.append(r["doc_type_label"])
        y_pred.append(pred_label)

    if not y_true:
        return {"error": "No labeled examples found"}

    labels = sorted(set(y_true + y_pred))
    report = classification_report(y_true, y_pred, labels=labels, output_dict=True, zero_division=0)
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "per_label": report,
        "n": len(y_true),
        "mismatches": [
            {"true": t, "pred": p}
            for t, p in zip(y_true, y_pred) if t != p
        ],
    }


def eval_industry(records: list[dict]) -> dict:
    """
    Multi-label accuracy for industry.
    A prediction is correct if the ground truth label appears anywhere
    in the predicted labels list.
    """
    n_correct = 0
    n_total = 0
    mismatches = []

    for r in records:
        if "industry_label" not in r:
            continue
        text = load_text_for_record(r)
        if not text:
            continue
        pred_labels, pred_probs = classify_industry(text)
        true_label = r["industry_label"]
        correct = true_label in pred_labels
        if correct:
            n_correct += 1
        else:
            mismatches.append({"true": true_label, "predicted": pred_labels})
        n_total += 1

    return {
        "top_k_accuracy": n_correct / n_total if n_total else 0,
        "n": n_total,
        "mismatches": mismatches,
        "note": "A prediction is counted correct if ground truth label appears in any predicted label.",
    }


def eval_pain_points_threshold(
    records: list[dict],
    thresholds: list[float] | None = None,
) -> dict:
    """
    Threshold calibration report for pain points.
    For each threshold, compute precision/recall vs. hand-labeled pain points.
    Returns a table so the best threshold can be empirically chosen.

    Note: pain_points ground truth is hand-labeled spot-check, not objective.
    This is threshold calibration, not accuracy measurement.
    """
    if thresholds is None:
        thresholds = [round(t, 2) for t in [x / 100 for x in range(40, 90, 5)]]

    labeled = [r for r in records if r.get("pain_points") is not None]
    if not labeled:
        return {"error": "No pain_points ground truth in labeled set. Add hand-labeled examples."}

    results_by_threshold = {}
    for threshold in thresholds:
        tp_total = fp_total = fn_total = 0
        for r in labeled:
            text = load_text_for_record(r)
            if not text:
                continue
            industry = r.get("industry_label", "other")
            predicted = detect_pain_points(text, industry=industry, threshold=threshold)
            pred_set = {p["label"].lower() for p in predicted}
            true_set = {p.lower() for p in r["pain_points"]}

            tp = len(pred_set & true_set)
            fp = len(pred_set - true_set)
            fn = len(true_set - pred_set)
            tp_total += tp
            fp_total += fp
            fn_total += fn

        precision = tp_total / (tp_total + fp_total) if (tp_total + fp_total) > 0 else 0
        recall = tp_total / (tp_total + fn_total) if (tp_total + fn_total) > 0 else 0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0
        results_by_threshold[str(threshold)] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }

    best_threshold = max(
        results_by_threshold.items(),
        key=lambda x: x[1]["f1"],
    )
    return {
        "note": (
            "This is threshold calibration, not accuracy. Pain points have no objective "
            "ground truth — hand-labeled examples reflect analyst judgment. "
            "Choose the threshold that maximizes F1 on your spot-check set."
        ),
        "n_labeled": len(labeled),
        "threshold_sweep": results_by_threshold,
        "best_threshold": best_threshold[0],
        "best_f1": best_threshold[1]["f1"],
    }


def eval_confidentiality(records: list[dict], rubric_override: str | None = None) -> dict:
    """
    Agreement rate vs. confidentiality_label (Sonnet-as-judge or hand-labeled).
    rubric_override replaces the default system prompt for ablation runs.
    """
    labeled = [r for r in records if r.get("confidentiality_label")]
    if not labeled:
        return {"error": "No confidentiality_label ground truth in labeled set."}

    y_true, y_pred = [], []
    for r in labeled:
        text = load_text_for_record(r)
        if not text:
            continue

        if rubric_override:
            original_prompt = conf_module.SYSTEM_PROMPT
            conf_module.SYSTEM_PROMPT = rubric_override
            try:
                result = classify_confidentiality(text)
            finally:
                conf_module.SYSTEM_PROMPT = original_prompt
        else:
            result = classify_confidentiality(text)

        y_true.append(r["confidentiality_label"])
        y_pred.append(result["label"])

    labels = sorted(set(y_true + y_pred))
    report = classification_report(y_true, y_pred, labels=labels, output_dict=True, zero_division=0)
    return {
        "agreement_rate": accuracy_score(y_true, y_pred),
        "per_label": report,
        "n": len(y_true),
        "mismatches": [
            {"true": t, "pred": p}
            for t, p in zip(y_true, y_pred) if t != p
        ],
        "note": (
            "Agreement rate vs. Sonnet-as-judge labels. Reflects inter-rater "
            "agreement, not objective accuracy — confidentiality is context-dependent."
        ),
    }


def eval_importance(records: list[dict], rubric_override: str | None = None) -> dict:
    """
    Agreement rate vs. importance_label (Sonnet-as-judge or hand-labeled).
    Uses confidentiality_label from the record if available.
    rubric_override replaces the default system prompt for ablation runs.
    """
    labeled = [r for r in records if r.get("importance_label")]
    if not labeled:
        return {"error": "No importance_label ground truth in labeled set."}

    y_true, y_pred = [], []
    for r in labeled:
        text = load_text_for_record(r)
        if not text:
            continue
        pain_points = [{"label": p, "similarity_score": 1.0} for p in r.get("pain_points", [])]
        confidentiality_label = r.get("confidentiality_label", "internal")

        if rubric_override:
            original_prompt = imp_module.SYSTEM_PROMPT
            imp_module.SYSTEM_PROMPT = rubric_override
            try:
                result = classify_importance(text, pain_points, confidentiality_label)
            finally:
                imp_module.SYSTEM_PROMPT = original_prompt
        else:
            result = classify_importance(text, pain_points, confidentiality_label)

        y_true.append(r["importance_label"])
        y_pred.append(result["label"])

    labels = sorted(set(y_true + y_pred))
    report = classification_report(y_true, y_pred, labels=labels, output_dict=True, zero_division=0)
    return {
        "agreement_rate": accuracy_score(y_true, y_pred),
        "per_label": report,
        "n": len(y_true),
        "mismatches": [
            {"true": t, "pred": p}
            for t, p in zip(y_true, y_pred) if t != p
        ],
        "note": (
            "Agreement rate vs. Sonnet-as-judge labels. This reflects inter-rater "
            "agreement, not objective accuracy — importance is inherently subjective."
        ),
    }


# ---------------------------------------------------------------------------
# Ablation runners
# ---------------------------------------------------------------------------

def _run_ablation_for(
    records: list[dict],
    rubric_paths: list[Path],
    eval_fn,
    baseline_label: str,
) -> dict:
    """Generic ablation runner: baseline + N rubric variants → comparison table."""
    results = {baseline_label: eval_fn(records)}

    for path in rubric_paths:
        rubric_text = path.read_text(encoding="utf-8")
        variant_name = path.stem
        logger.info(f"Evaluating rubric variant: {variant_name}")
        results[variant_name] = eval_fn(records, rubric_override=rubric_text)

    comparison = {
        name: {"agreement_rate": r.get("agreement_rate"), "n": r.get("n")}
        for name, r in results.items()
    }
    return {
        "comparison_table": comparison,
        "best_variant": max(comparison.items(), key=lambda x: x[1]["agreement_rate"] or 0)[0],
        "full_results": results,
    }


ABLATION_DIMENSIONS = {
    "importance": eval_importance,
    "confidentiality": eval_confidentiality,
}


def run_ablation(records: list[dict], rubric_paths: list[Path], dimension: str) -> dict:
    eval_fn = ABLATION_DIMENSIONS[dimension]
    logger.info(f"Evaluating baseline {dimension} rubric...")
    return _run_ablation_for(records, rubric_paths, eval_fn, "baseline")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@app.command()
def main(
    data: Path = typer.Option(..., "--data", help="JSONL labeled eval set"),
    mode: str = typer.Option(
        "full",
        "--mode",
        help="Eval mode: full | threshold-calibration | ablation",
    ),
    dimension: str = typer.Option(
        "importance",
        "--dimension",
        help="Dimension to ablate (ablation mode only): importance | confidentiality",
    ),
    rubric_variants: Optional[list[Path]] = typer.Option(
        None,
        "--rubric-variants",
        help="Rubric variant files for ablation mode",
    ),
    output_dir: Path = typer.Option(EVAL_OUTPUT_DIR, "--output-dir"),
):
    """Run the eval harness and write a report."""
    if not data.exists():
        typer.echo(f"ERROR: Labeled set not found: {data}", err=True)
        raise typer.Exit(1)

    records = load_labeled_set(data)
    logger.info(f"Loaded {len(records)} labeled records")

    report: dict = {
        "eval_timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        "n_records": len(records),
        "mode": mode,
    }

    if mode == "full":
        typer.echo("Evaluating document_type classifier...")
        report["doc_type"] = eval_doc_type(records)

        typer.echo("Evaluating industry classifier...")
        report["industry"] = eval_industry(records)

        typer.echo("Calibrating pain point threshold...")
        report["pain_points"] = eval_pain_points_threshold(records)

        typer.echo("Evaluating confidentiality classification...")
        report["confidentiality"] = eval_confidentiality(records)

        typer.echo("Evaluating importance classification...")
        report["importance"] = eval_importance(records)

    elif mode == "threshold-calibration":
        typer.echo("Running pain point threshold calibration sweep...")
        report["pain_points"] = eval_pain_points_threshold(records)

    elif mode == "ablation":
        if not rubric_variants:
            typer.echo("ERROR: --rubric-variants required for ablation mode", err=True)
            raise typer.Exit(1)
        if dimension not in ABLATION_DIMENSIONS:
            typer.echo(f"ERROR: --dimension must be one of: {', '.join(ABLATION_DIMENSIONS)}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Running {dimension} rubric ablation ({len(rubric_variants)} variants)...")
        report["ablation"] = run_ablation(records, rubric_variants, dimension)

    else:
        typer.echo(f"ERROR: Unknown mode: {mode}", err=True)
        raise typer.Exit(1)

    # Console summary
    typer.echo("\n" + "=" * 60)
    typer.echo("EVAL REPORT SUMMARY")
    typer.echo("=" * 60)

    if "doc_type" in report:
        dt = report["doc_type"]
        typer.echo(f"  doc_type accuracy:           {dt.get('accuracy', 'N/A'):.3f}  (n={dt.get('n', 0)})")
    if "industry" in report:
        ind = report["industry"]
        typer.echo(f"  industry top-k accuracy:     {ind.get('top_k_accuracy', 'N/A'):.3f}  (n={ind.get('n', 0)})")
    if "pain_points" in report and "best_threshold" in report["pain_points"]:
        pp = report["pain_points"]
        typer.echo(f"  pain_points best threshold:  {pp['best_threshold']}  (F1={pp['best_f1']:.3f})")
    if "confidentiality" in report:
        conf = report["confidentiality"]
        typer.echo(f"  confidentiality agreement:   {conf.get('agreement_rate', 'N/A'):.3f}  (n={conf.get('n', 0)})")
    if "importance" in report:
        imp = report["importance"]
        typer.echo(f"  importance agreement rate:   {imp.get('agreement_rate', 'N/A'):.3f}  (n={imp.get('n', 0)})")
    if "ablation" in report:
        ab = report["ablation"]
        typer.echo(f"  best rubric variant ({dimension}): {ab['best_variant']}")
        for variant, metrics in ab["comparison_table"].items():
            typer.echo(f"    {variant}: agreement={metrics['agreement_rate']:.3f}")

    # Save report
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"report_{ts}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    typer.echo(f"\nFull report saved to {report_path}")


if __name__ == "__main__":
    app()
