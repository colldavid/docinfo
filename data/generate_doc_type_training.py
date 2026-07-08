"""
Generate synthetic training docs for under-represented doc_type classes.
Appends to data/edgar/labeled_samples.jsonl (same format as EDGAR data).

Target: balance the training set across all doc types.

Usage:
    PYTHONPATH=. .venv/Scripts/python data/generate_doc_type_training.py
    PYTHONPATH=. .venv/Scripts/python data/generate_doc_type_training.py --count 50 --workers 15
"""
import argparse
import asyncio
import json
import random
from pathlib import Path

import anthropic
from app.config import settings

OUTPUT = Path(__file__).parent / "edgar" / "labeled_samples.jsonl"

# (doc_type_label, industry, description of what to generate)
TARGETS = [
    # press_release — need more variety (not just earnings)
    ("press_release", "technology",      "product launch announcement for a new enterprise software platform"),
    ("press_release", "healthcare",      "FDA approval announcement for a new drug or medical device"),
    ("press_release", "finance",         "bank merger or acquisition announcement"),
    ("press_release", "energy",          "oil & gas company operational update or asset sale announcement"),
    ("press_release", "retail",          "retail chain expansion or store closure announcement"),
    ("press_release", "manufacturing",   "plant opening or production milestone press release"),
    ("press_release", "real_estate",     "REIT property acquisition or development announcement"),
    ("press_release", "telecommunications", "network expansion or spectrum acquisition announcement"),

    # regulatory_filing — almost none in training
    ("regulatory_filing", "finance",         "SEC proxy statement (DEF 14A) for annual shareholder meeting"),
    ("regulatory_filing", "healthcare",      "FDA 510(k) premarket notification for a medical device"),
    ("regulatory_filing", "energy",          "FERC rate filing or pipeline certificate application"),
    ("regulatory_filing", "technology",      "FCC spectrum license application or renewal"),
    ("regulatory_filing", "finance",         "SEC S-1 registration statement for IPO"),
    ("regulatory_filing", "manufacturing",   "EPA environmental compliance report"),
    ("regulatory_filing", "telecommunications", "FCC Form 499 telecommunications reporting filing"),
    ("regulatory_filing", "real_estate",     "HUD multifamily housing regulatory agreement"),

    # contract — zero in training
    ("contract", "technology",      "software as a service (SaaS) master services agreement"),
    ("contract", "healthcare",      "hospital vendor services agreement for medical supplies"),
    ("contract", "finance",         "investment management agreement between fund and adviser"),
    ("contract", "energy",          "natural gas purchase and sale agreement"),
    ("contract", "retail",          "retail space lease agreement for commercial property"),
    ("contract", "manufacturing",   "supply chain manufacturing and distribution agreement"),
    ("contract", "real_estate",     "commercial real estate purchase and sale agreement"),
    ("contract", "defense",         "government defense contractor services agreement"),
    ("contract", "technology",      "software licensing and maintenance agreement"),
    ("contract", "healthcare",      "physician employment agreement with hospital system"),

    # internal_memo — zero in training
    ("internal_memo", "technology",      "engineering team memo about system architecture decision"),
    ("internal_memo", "finance",         "internal memo from CFO about Q3 budget reallocation"),
    ("internal_memo", "healthcare",      "hospital operations memo about staffing changes"),
    ("internal_memo", "retail",          "store operations memo about holiday season procedures"),
    ("internal_memo", "manufacturing",   "plant manager memo about safety protocol update"),
    ("internal_memo", "energy",          "internal memo about refinery maintenance shutdown"),
    ("internal_memo", "real_estate",     "property management memo about tenant policy changes"),
    ("internal_memo", "technology",      "product team memo about roadmap prioritization"),
    ("internal_memo", "finance",         "risk committee memo about credit exposure limits"),
    ("internal_memo", "healthcare",      "clinical operations memo about EHR system migration"),

    # research_report — zero in training
    ("research_report", "technology",    "equity research report on semiconductor industry outlook"),
    ("research_report", "healthcare",    "market research report on pharmaceutical pricing trends"),
    ("research_report", "finance",       "fixed income research report on credit markets"),
    ("research_report", "energy",        "commodity research report on oil price forecasts"),
    ("research_report", "retail",        "consumer research report on e-commerce trends"),
    ("research_report", "real_estate",   "real estate market research report on commercial vacancy rates"),
    ("research_report", "manufacturing", "industry research report on supply chain resilience"),
    ("research_report", "telecommunications", "telecom sector research report on 5G adoption"),

    # onboarding / hr — zero in training (maps to internal_memo but distinct)
    ("internal_memo", "technology",      "new employee onboarding guide for software engineers"),
    ("internal_memo", "finance",         "employee handbook section on expense reimbursement policy"),
    ("internal_memo", "healthcare",      "clinical staff onboarding and credentialing guide"),
]

SYSTEM_PROMPT = """\
You are generating realistic synthetic business documents for a training dataset. \
Write a realistic, detailed document of the specified type. Use plausible company names, \
numbers, dates, and industry-specific language. The document should be 400-700 words. \
Output ONLY the document text — no titles, no labels, no commentary.\
"""


async def generate_one(
    client: anthropic.AsyncAnthropic,
    semaphore: asyncio.Semaphore,
    doc_type: str,
    industry: str,
    description: str,
    idx: int,
    total: int,
) -> dict | None:
    async with semaphore:
        try:
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                temperature=0.9,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"Write a {doc_type.replace('_', ' ')} document: {description}. Industry: {industry}."
                }],
            )
            text = response.content[0].text.strip()
            if len(text) < 200:
                print(f"  [{idx}/{total}] too short, skipping")
                return None
            print(f"  [{idx}/{total}] OK  {doc_type:<18} {industry}")
            return {"text": text, "doc_type_label": doc_type, "industry_label": industry}
        except Exception as e:
            print(f"  [{idx}/{total}] ERROR: {e}")
            return None


async def run(count_per_target: int, workers: int):
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    semaphore = asyncio.Semaphore(workers)

    # Expand targets: repeat each target to hit count_per_target
    work = []
    for doc_type, industry, description in TARGETS:
        for _ in range(count_per_target):
            work.append((doc_type, industry, description))

    # Shuffle so we don't hammer one class at a time
    random.shuffle(work)
    total = len(work)
    print(f"Generating {total} documents ({len(TARGETS)} targets × {count_per_target} each)…")

    tasks = [
        generate_one(client, semaphore, doc_type, industry, description, i + 1, total)
        for i, (doc_type, industry, description) in enumerate(work)
    ]
    results = await asyncio.gather(*tasks)

    records = [r for r in results if r is not None]
    print(f"\nWriting {len(records)} records to {OUTPUT}…")

    with open(OUTPUT, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    # Summary
    from collections import Counter
    counts = Counter(r["doc_type_label"] for r in records)
    print("\nGenerated by doc type:")
    for k, v in counts.most_common():
        print(f"  {v:>4}  {k}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=8,
                        help="Number of docs to generate per target description (default 8)")
    parser.add_argument("--workers", type=int, default=15,
                        help="Concurrent Haiku requests (default 15)")
    args = parser.parse_args()
    asyncio.run(run(args.count, args.workers))


if __name__ == "__main__":
    main()
