"""
Generate a synthetic labeled evaluation set for DocInfo.

Creates 400 documents — 100 per confidentiality level (public / internal /
confidential / restricted) — each with a different injected pain point and
industry. Uses async workers for speed. Resumes from existing output file.

Usage:
    PYTHONPATH=. python eval/generate_synthetic.py
    PYTHONPATH=. python eval/generate_synthetic.py --output eval/synthetic_labeled.jsonl
    PYTHONPATH=. python eval/generate_synthetic.py --count 40 --workers 5

Output JSONL format (one record per line):
{
  "text": "...",
  "filepath": null,
  "doc_type_label": "financial_report",
  "industry_label": "technology",
  "pain_points": ["supply chain delay"],
  "confidentiality_label": "confidential",
  "importance_label": "high",
  "synthetic": true
}
"""

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Optional

import anthropic
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_FILE = Path("eval/synthetic_labeled.jsonl")
WORKERS = 12

CONFIDENTIALITY_LEVELS = ["public", "internal", "confidential", "restricted"]

DOC_TYPES = ["financial_report", "press_release", "regulatory_filing"]

INDUSTRIES = [
    "technology", "healthcare", "finance", "energy", "retail",
    "manufacturing", "real_estate", "telecommunications", "defense",
    "media", "transportation", "education",
]

PAIN_POINTS = [
    "supply chain delay", "talent retention", "margin compression",
    "regulatory compliance burden", "cybersecurity breach", "liquidity risk",
    "customer churn", "product launch failure", "litigation exposure",
    "ESG reporting gap", "debt covenant risk", "currency risk",
    "procurement inefficiency", "integration failure post-acquisition",
    "rising input costs", "workforce restructuring", "IP dispute",
    "revenue concentration risk", "contract renewal risk", "pension liability",
    "supply chain concentration", "export control violation", "data privacy violation",
    "reputational damage", "competitive displacement", "technology obsolescence",
    "delayed regulatory approval", "working capital shortfall",
    "executive departure", "accounting restatement risk",
    "environmental liability", "product recall",
    "channel conflict", "distribution bottleneck", "geopolitical exposure",
    "interest rate sensitivity", "credit downgrade risk", "vendor lock-in",
    "operational downtime", "ERP implementation failure",
]

CONFIDENTIALITY_DESCRIPTIONS = {
    "public": (
        "This is a PUBLIC document — press release, published earnings announcement, "
        "or other content explicitly released to the general public. "
        "No sensitive information, financials, or internal details. Anyone can read this."
    ),
    "internal": (
        "This is an INTERNAL document — meant for employees only but not particularly "
        "sensitive. Could be a town-hall memo, internal newsletter, or operational update. "
        "Not confidential, but not publicly released. Mentions internal KPIs or processes."
    ),
    "confidential": (
        "This is a CONFIDENTIAL document — contains sensitive business information "
        "shared only with specific clients or internal leadership. Could be a client briefing, "
        "M&A analysis, or strategic plan. Marked confidential. Contains non-public financials, "
        "strategic details, or client-specific data."
    ),
    "restricted": (
        "This is a RESTRICTED document — the most sensitive tier. Contains material "
        "non-public information, trade secrets, or regulatory-sensitive data. "
        "Examples: pre-announcement earnings, active deal memos, legal settlement terms. "
        "Strict need-to-know distribution. Explicitly labelled RESTRICTED or contains "
        "attorney-client privilege / regulatory embargo language."
    ),
}

GENERATION_PROMPT = """\
You are writing a realistic consulting-sector business document for a synthetic evaluation dataset.

Document requirements:
- Type: {doc_type}
- Industry: {industry}
- Confidentiality tier: {confidentiality_level}
- {confidentiality_description}
- The document should naturally contain evidence of the following business pain point: "{pain_point}"
- Length: 350–500 words
- Tone: professional, realistic — as if written by a consulting firm or corporate team
- Do NOT include any meta-commentary, preamble, or labels — write the document itself

Write the document now:"""

CONFIDENTIALITY_TO_IMPORTANCE = {
    "public": "low",
    "internal": "low",
    "confidential": "medium",
    "restricted": "high",
}


def build_work_items(count: int, already_done: int) -> list[dict]:
    """Build the full list of (level, doc_type, industry, pain_point) tasks, skip already done."""
    random.seed(42)
    pain_points = PAIN_POINTS.copy()
    random.shuffle(pain_points)

    per_level = count // len(CONFIDENTIALITY_LEVELS)
    items = []
    idx = 0
    for level in CONFIDENTIALITY_LEVELS:
        for i in range(per_level):
            items.append({
                "level": level,
                "doc_type": DOC_TYPES[i % len(DOC_TYPES)],
                "industry": INDUSTRIES[i % len(INDUSTRIES)],
                "pain_point": pain_points[idx % len(pain_points)],
            })
            idx += 1

    return items[already_done:]


async def generate_one(client: anthropic.AsyncAnthropic, item: dict) -> Optional[dict]:
    prompt = GENERATION_PROMPT.format(
        doc_type=item["doc_type"],
        industry=item["industry"],
        confidentiality_level=item["level"],
        confidentiality_description=CONFIDENTIALITY_DESCRIPTIONS[item["level"]],
        pain_point=item["pain_point"],
    )
    try:
        msg = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        return {
            "text": text,
            "filepath": None,
            "doc_type_label": item["doc_type"],
            "industry_label": item["industry"],
            "injected_pain_point": item["pain_point"],
            "pain_points": [item["pain_point"]],
            "confidentiality_label": item["level"],
            "importance_label": CONFIDENTIALITY_TO_IMPORTANCE[item["level"]],
            "synthetic": True,
        }
    except Exception as e:
        logger.error(f"Generation failed for {item}: {e}")
        return None


async def run(output: Path, count: int, workers: int):
    output.parent.mkdir(parents=True, exist_ok=True)

    # Count already-written records so we can resume
    already_done = 0
    if output.exists():
        with open(output, encoding="utf-8") as f:
            already_done = sum(1 for line in f if line.strip())
        logger.info(f"Resuming — {already_done} records already written, skipping those")

    work_items = build_work_items(count, already_done)
    remaining = len(work_items)

    if remaining == 0:
        logger.info("Nothing to do — output already complete.")
        return

    logger.info(f"Generating {remaining} documents with {workers} async workers → {output}")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    semaphore = asyncio.Semaphore(workers)
    written = already_done
    lock = asyncio.Lock()

    async def bounded(item: dict, out_file):
        nonlocal written
        async with semaphore:
            record = await generate_one(client, item)
        if record:
            async with lock:
                out_file.write(json.dumps(record) + "\n")
                out_file.flush()
                written += 1
                if written % 20 == 0:
                    logger.info(f"Progress: {written}/{count} written")

    with open(output, "a", encoding="utf-8") as out_file:
        tasks = [bounded(item, out_file) for item in work_items]
        await asyncio.gather(*tasks)

    logger.info(f"Done. Wrote {written} total records to {output}")


def main(output: Path = OUTPUT_FILE, count: int = 400, workers: int = WORKERS):
    asyncio.run(run(output, count, workers))


if __name__ == "__main__":
    import typer

    def _main(
        output: Path = typer.Option(OUTPUT_FILE, "--output", "-o"),
        count: int = typer.Option(400, "--count", "-n"),
        workers: int = typer.Option(WORKERS, "--workers", "-w"),
    ):
        main(output=output, count=count, workers=workers)

    typer.run(_main)
