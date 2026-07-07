"""
DocInfo CLI — point at a folder, get back JSON + CSV.

Usage:
    python cli.py classify ./my_documents/
    python cli.py classify ./my_documents/ --output results.json --csv results.csv
    python cli.py classify ./my_documents/ --threshold 0.7
"""

import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from app.ingestion import ingest_folder
from app.classify import classify_document
from app.models import ClassificationResult

app = typer.Typer(help="DocInfo: document intelligence and classification tool.")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _result_to_dict(result: ClassificationResult) -> dict:
    """Serialize a ClassificationResult to the canonical JSON output format."""
    return {
        "filename": result.filename,
        "document_type": {
            "label": result.document_type.label,
            "probability": result.document_type.probability,
        },
        "industry": {
            "labels": result.industry.labels,
            "probabilities": result.industry.probabilities,
        },
        "pain_points": [
            {"label": p.label, "similarity_score": p.similarity_score}
            for p in result.pain_points
        ],
        "confidentiality": {
            "label": result.confidentiality.label,
            "rationale": result.confidentiality.rationale,
            "confidence": result.confidentiality.confidence,
            "needs_review": result.confidentiality.needs_review,
        } if result.confidentiality else None,
        "importance_level": {
            "label": result.importance_level.label,
            "rationale": result.importance_level.rationale,
            "confidence": result.importance_level.confidence,
            "needs_review": result.importance_level.needs_review,
        },
        "classified_at": result.classified_at.isoformat(),
        "error": result.error,
    }


def _write_csv(results: list[ClassificationResult], csv_path: Path) -> None:
    """Write a flattened CSV view of results for quick spreadsheet use."""
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "filename",
            "doc_type_label",
            "doc_type_probability",
            "industry_labels",
            "industry_probabilities",
            "pain_points",
            "pain_point_scores",
            "confidentiality_label",
            "confidentiality_confidence",
            "confidentiality_needs_review",
            "importance_label",
            "importance_confidence",
            "importance_needs_review",
            "rationale",
            "classified_at",
            "error",
        ])
        for r in results:
            writer.writerow([
                r.filename,
                r.document_type.label if r.document_type else "",
                r.document_type.probability if r.document_type else "",
                "|".join(r.industry.labels) if r.industry else "",
                "|".join(str(p) for p in r.industry.probabilities) if r.industry else "",
                "|".join(p.label for p in r.pain_points),
                "|".join(str(p.similarity_score) for p in r.pain_points),
                r.confidentiality.label if r.confidentiality else "",
                r.confidentiality.confidence if r.confidentiality else "",
                r.confidentiality.needs_review if r.confidentiality else "",
                r.importance_level.label if r.importance_level else "",
                r.importance_level.confidence if r.importance_level else "",
                r.importance_level.needs_review if r.importance_level else "",
                r.importance_level.rationale if r.importance_level else "",
                r.classified_at.isoformat() if r.classified_at else "",
                r.error or "",
            ])


@app.command()
def classify(
    folder: Path = typer.Argument(..., help="Folder containing documents to classify"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="JSON output file (default: stdout)"),
    csv_output: Optional[Path] = typer.Option(None, "--csv", help="Optional CSV export path"),
    threshold: Optional[float] = typer.Option(None, "--threshold", help="Pain point similarity threshold (0-1)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Classify all documents in FOLDER across all dimensions."""
    _setup_logging(verbose)

    if not folder.exists() or not folder.is_dir():
        typer.echo(f"ERROR: {folder} is not a valid directory.", err=True)
        raise typer.Exit(1)

    # Override threshold if provided
    if threshold is not None:
        from app import config
        config.settings.pain_point_threshold = threshold
        typer.echo(f"Pain point threshold set to {threshold}")

    typer.echo(f"Ingesting documents from {folder}...")
    docs = ingest_folder(folder)

    if not docs:
        typer.echo("No documents found or parsed successfully.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Classifying {len(docs)} document(s)...")
    results: list[ClassificationResult] = []

    for i, (path, text) in enumerate(docs, 1):
        typer.echo(f"  [{i}/{len(docs)}] {path.name}")
        try:
            result = classify_document(path, text)
        except Exception as e:
            logging.getLogger(__name__).error(f"Classification failed for {path.name}: {e}")
            from datetime import timezone
            result = ClassificationResult(
                filename=path.name,
                document_type=None,
                industry=None,
                pain_points=[],
                importance_level=None,
                classified_at=datetime.now(timezone.utc),
                error=str(e),
            )
        results.append(result)

    output_data = [_result_to_dict(r) for r in results]

    if output:
        output.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
        typer.echo(f"Results written to {output}")
    else:
        typer.echo(json.dumps(output_data, indent=2))

    if csv_output:
        _write_csv(results, csv_output)
        typer.echo(f"CSV written to {csv_output}")

    needs_review = [r for r in results if r.importance_level and r.importance_level.needs_review]
    if needs_review:
        typer.echo(f"\n{len(needs_review)} document(s) flagged for review:")
        for r in needs_review:
            typer.echo(f"  - {r.filename}")


if __name__ == "__main__":
    app()
