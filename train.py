"""
Train logistic regression classifiers for document_type and industry.
Run once, commit the saved model files to the repo.

Usage:
    python train.py [--data data/edgar/labeled_samples.jsonl] [--model-dir model/]

After training, the following files are written to model/:
    doc_type_model.joblib    — trained LogisticRegression for document type
    doc_type_encoder.joblib  — LabelEncoder for document type labels
    industry_model.joblib    — trained LogisticRegression for industry
    industry_encoder.joblib  — LabelEncoder for industry labels
    training_report.json     — per-label accuracy metrics for both classifiers

Teammates who clone the repo get these files and never need to run train.py.
Re-run only when expanding the EDGAR dataset or adding new label categories.
"""

import json
import logging
import time
from pathlib import Path

import joblib
import numpy as np
import typer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report, accuracy_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = typer.Typer()


def load_samples(data_file: Path) -> tuple[list[str], list[str], list[str]]:
    """Load JSONL samples. Returns (texts, doc_type_labels, industry_labels)."""
    texts, doc_types, industries = [], [], []
    skipped = 0
    with open(data_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if not row.get("text") or not row.get("doc_type_label"):
                    skipped += 1
                    continue
                texts.append(row["text"])
                doc_types.append(row["doc_type_label"])
                industries.append(row.get("industry_label") or "other")
            except json.JSONDecodeError:
                skipped += 1
    if skipped:
        logger.warning(f"Skipped {skipped} malformed records")
    logger.info(f"Loaded {len(texts)} training samples")
    return texts, doc_types, industries


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed all texts. Logs progress — this is the slow part."""
    from sentence_transformers import SentenceTransformer
    from app.config import settings

    logger.info(f"Loading embedding model: {settings.embedding_model}")
    model = SentenceTransformer(settings.embedding_model)

    logger.info(f"Embedding {len(texts)} texts (this may take a few minutes)...")
    start = time.time()
    embeddings = model.encode(
        texts,
        convert_to_numpy=True,
        show_progress_bar=True,
        batch_size=64,
    )
    elapsed = time.time() - start
    logger.info(f"Embedding complete in {elapsed:.1f}s")
    return embeddings


def train_classifier(
    embeddings: np.ndarray,
    labels: list[str],
    label_name: str,
    test_size: float = 0.2,
    random_state: int = 42,
) -> tuple[LogisticRegression, LabelEncoder, dict]:
    """
    Train a logistic regression classifier. Returns (model, encoder, report_dict).
    Uses lbfgs solver with L2 regularization — fast and well-calibrated for this task.
    """
    encoder = LabelEncoder()
    y = encoder.fit_transform(labels)

    label_counts = {l: labels.count(l) for l in set(labels)}
    logger.info(f"\n{label_name} label distribution: {label_counts}")

    # Stratify only when every class has >= 2 examples; fall back otherwise
    min_class_count = min(np.bincount(y))
    use_stratify = y if min_class_count >= 2 else None
    X_train, X_test, y_train, y_test = train_test_split(
        embeddings, y, test_size=test_size, random_state=random_state, stratify=use_stratify
    )

    logger.info(f"Training {label_name} classifier ({X_train.shape[0]} train, {X_test.shape[0]} test)...")
    model = LogisticRegression(
        max_iter=1000,
        solver="lbfgs",
        multi_class="multinomial",
        C=1.0,
        random_state=random_state,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    # Use only classes present in the test set to avoid target_names mismatch
    present_labels = sorted(set(y_test) | set(y_pred))
    present_names = encoder.inverse_transform(present_labels)
    report = classification_report(
        y_test, y_pred,
        labels=present_labels,
        target_names=present_names,
        output_dict=True,
        zero_division=0,
    )

    logger.info(f"\n{label_name} accuracy: {acc:.3f}")
    logger.info(classification_report(y_test, y_pred, labels=present_labels, target_names=present_names, zero_division=0))

    return model, encoder, {"accuracy": acc, "per_label": report}


@app.command()
def main(
    data_file: Path = typer.Option(
        Path("data/edgar/labeled_samples.jsonl"),
        "--data",
        help="Path to JSONL file produced by data/edgar_download.py",
    ),
    model_dir: Path = typer.Option(
        Path("model"),
        "--model-dir",
        help="Directory to save trained model files",
    ),
    min_samples: int = typer.Option(
        20,
        "--min-samples",
        help="Minimum samples required to train (lower for smoke tests)",
    ),
):
    """Train classifiers on EDGAR data and save model files."""
    if not data_file.exists():
        typer.echo(
            f"ERROR: Training data not found at {data_file}\n"
            "Run `python data/edgar_download.py` first to download EDGAR filings.",
            err=True,
        )
        raise typer.Exit(1)

    model_dir.mkdir(parents=True, exist_ok=True)

    texts, doc_types, industries = load_samples(data_file)

    if len(texts) < min_samples:
        typer.echo(
            f"ERROR: Only {len(texts)} samples found. Need at least {min_samples} to train.\n"
            "Re-run data/edgar_download.py with a larger SAMPLES_PER_FORM value.",
            err=True,
        )
        raise typer.Exit(1)

    embeddings = embed_texts(texts)

    # Train document type classifier
    doc_type_model, doc_type_encoder, doc_type_report = train_classifier(
        embeddings, doc_types, "document_type"
    )
    joblib.dump(doc_type_model, model_dir / "doc_type_model.joblib")
    joblib.dump(doc_type_encoder, model_dir / "doc_type_encoder.joblib")
    logger.info(f"Saved doc_type model to {model_dir}/")

    # Train industry classifier
    # Filter out samples with no industry label
    valid_mask = [i for i, ind in enumerate(industries) if ind and ind != "other" or True]
    ind_embeddings = embeddings[valid_mask]
    ind_labels = [industries[i] for i in valid_mask]

    industry_model, industry_encoder, industry_report = train_classifier(
        ind_embeddings, ind_labels, "industry"
    )
    joblib.dump(industry_model, model_dir / "industry_model.joblib")
    joblib.dump(industry_encoder, model_dir / "industry_encoder.joblib")
    logger.info(f"Saved industry model to {model_dir}/")

    # Save training report
    report = {
        "training_samples": len(texts),
        "doc_type": doc_type_report,
        "industry": industry_report,
    }
    report_path = model_dir / "training_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Training report saved to {report_path}")

    typer.echo(
        f"\nTraining complete.\n"
        f"  doc_type accuracy:  {doc_type_report['accuracy']:.3f}\n"
        f"  industry accuracy:  {industry_report['accuracy']:.3f}\n"
        f"\nCommit the model/ directory to share with your team:\n"
        f"  git add model/\n"
        f"  git commit -m 'Add trained classifiers'\n"
    )


if __name__ == "__main__":
    app()
