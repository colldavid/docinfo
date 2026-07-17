"""
Fine-tune the sentence-transformer embedding model on labeled training data
using MultipleNegativesRankingLoss (contrastive).

Each training step: anchor + positive are two different docs of the same class.
All other items in the batch serve as in-batch negatives. This pulls same-class
embeddings together and pushes different-class embeddings apart.

After fine-tuning, the model is saved to model/embedding/ and train.py
will use it automatically if settings.embedding_model points there.

Usage:
    PYTHONPATH=. .venv/Scripts/python finetune.py
    PYTHONPATH=. .venv/Scripts/python finetune.py --epochs 3 --batch-size 32 --task doc_type
    PYTHONPATH=. .venv/Scripts/python finetune.py --task industry
    PYTHONPATH=. .venv/Scripts/python finetune.py --task both
"""
import argparse
import json
import logging
import random
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

JSONL = Path("data/edgar/labeled_samples.jsonl")
MODEL_OUT = Path("model/embedding")
BASE_MODEL = "all-MiniLM-L6-v2"

# How much of each text to use (characters) — keeps memory reasonable
TEXT_CHARS = 256


def load_records(task: str) -> dict[str, list[str]]:
    """Returns {label: [text, ...]} for the given task (doc_type or industry)."""
    label_key = "doc_type_label" if task == "doc_type" else "industry_label"
    lines = JSONL.read_text(encoding="utf-8").splitlines()
    by_label: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        if not line.strip():
            continue
        r = json.loads(line)
        label = r.get(label_key)
        text = r.get("text", "").strip()
        if label and text:
            by_label[label].append(text[:TEXT_CHARS])
    return dict(by_label)


def build_pairs(by_label: dict[str, list[str]], pairs_per_class: int = 200) -> list[tuple[str, str]]:
    """
    Build (anchor, positive) pairs — both from the same class.
    Shuffle within each class so we get diverse pairs.
    """
    pairs = []
    for label, texts in by_label.items():
        if len(texts) < 2:
            continue
        random.shuffle(texts)
        # cycle through texts to create pairs_per_class pairs
        for i in range(pairs_per_class):
            a = texts[i % len(texts)]
            b = texts[(i + 1) % len(texts)]
            if a != b:
                pairs.append((a, b))
    random.shuffle(pairs)
    logger.info(f"Built {len(pairs)} training pairs across {len(by_label)} classes")
    return pairs


def finetune(task: str, epochs: int, batch_size: int, warmup_ratio: float = 0.1):
    from sentence_transformers import SentenceTransformer, InputExample, losses
    from torch.utils.data import DataLoader

    logger.info(f"Loading base model: {BASE_MODEL}")
    model = SentenceTransformer(BASE_MODEL)

    by_label = load_records(task)
    logger.info(f"Loaded {sum(len(v) for v in by_label.values())} texts across {len(by_label)} {task} classes")

    pairs = build_pairs(by_label, pairs_per_class=300)
    examples = [InputExample(texts=[a, b]) for a, b in pairs]

    loader = DataLoader(examples, shuffle=True, batch_size=batch_size)
    loss = losses.MultipleNegativesRankingLoss(model)

    warmup_steps = int(len(loader) * epochs * warmup_ratio)
    total_steps = len(loader) * epochs
    logger.info(f"Training: {total_steps} steps, {warmup_steps} warmup, batch_size={batch_size}, epochs={epochs}")

    out_path = str(MODEL_OUT / task)
    model.fit(
        train_objectives=[(loader, loss)],
        epochs=epochs,
        warmup_steps=warmup_steps,
        output_path=out_path,
        show_progress_bar=True,
        checkpoint_save_steps=0,  # no intermediate checkpoints
    )
    logger.info(f"Saved fine-tuned model to {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=["doc_type", "industry", "both"], default="both",
                        help="Which task to fine-tune for. 'both' trains one model per task.")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    MODEL_OUT.mkdir(parents=True, exist_ok=True)

    if args.task == "both":
        # Train a single combined model on all labels from both tasks
        # This gives a shared embedding space that's good for both classifiers
        logger.info("Fine-tuning combined model on doc_type + industry labels...")
        from sentence_transformers import SentenceTransformer, InputExample, losses
        from torch.utils.data import DataLoader

        model = SentenceTransformer(BASE_MODEL)
        all_pairs = []
        for task in ["doc_type", "industry"]:
            by_label = load_records(task)
            logger.info(f"  {task}: {sum(len(v) for v in by_label.values())} texts, {len(by_label)} classes")
            all_pairs.extend(build_pairs(by_label, pairs_per_class=100))

        random.shuffle(all_pairs)
        examples = [InputExample(texts=[a, b]) for a, b in all_pairs]
        loader = DataLoader(examples, shuffle=True, batch_size=args.batch_size)
        loss = losses.MultipleNegativesRankingLoss(model)
        warmup_steps = int(len(loader) * args.epochs * 0.1)

        logger.info(f"Training combined model: {len(all_pairs)} pairs, {args.epochs} epochs")
        out_path = str(MODEL_OUT / "combined")
        model.fit(
            train_objectives=[(loader, loss)],
            epochs=args.epochs,
            warmup_steps=warmup_steps,
            output_path=out_path,
            show_progress_bar=True,
            checkpoint_save_steps=0,
        )
        logger.info(f"Saved combined model to {out_path}")
        logger.info("\nNow retrain classifiers with:")
        logger.info(f"  PYTHONPATH=. .venv/Scripts/python train.py --embedding-model {out_path}")
    else:
        out_path = finetune(args.task, epochs=args.epochs, batch_size=args.batch_size)
        logger.info("\nNow retrain classifiers with:")
        logger.info(f"  PYTHONPATH=. .venv/Scripts/python train.py --embedding-model {out_path}")


if __name__ == "__main__":
    main()
