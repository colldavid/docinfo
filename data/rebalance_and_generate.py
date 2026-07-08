"""
Rebalance labeled_samples.jsonl and generate consulting-style synthetic docs.

Steps:
1. Cap EDGAR financial_reports at MAX_EDGAR_FINANCIAL (keep all other EDGAR types)
2. Add sample docs from documents/samples/ with correct labels
3. Generate consulting-style synthetic docs to bring each doc_type to TARGET
4. Write new labeled_samples.jsonl (replaces existing)
5. Re-run train.py

Usage:
    PYTHONPATH=. .venv/Scripts/python data/rebalance_and_generate.py --workers 20
"""
import argparse
import asyncio
import json
import random
from collections import Counter
from pathlib import Path

import anthropic
from app.config import settings

JSONL = Path(__file__).parent / "edgar" / "labeled_samples.jsonl"
SAMPLES_DIR = Path(__file__).parent.parent / "documents" / "samples"

MAX_EDGAR_FINANCIAL = 150
TARGET_PER_DOC_TYPE = 200

# Correct labels for the sample docs
SAMPLE_LABELS = {
    "acme_q3_financials.txt":          ("financial_report",   "finance"),
    "horizon_market_research.txt":     ("research_report",    "technology"),
    "medicore_merger_announcement.txt":("press_release",      "healthcare"),
    "onboarding_guide.txt":            ("internal_memo",      "technology"),
    "pharma_pipeline_update.txt":      ("research_report",    "healthcare"),
}

# Consulting-style generation targets: (doc_type, industry, description)
# These are short, client-facing, plaintext consulting documents — NOT SEC filings
CONSULTING_TEMPLATES = {
    "financial_report": [
        ("finance",      "draft Q3 earnings summary for a mid-size consumer goods company, written as a short internal brief for management — not an SEC filing"),
        ("healthcare",   "quarterly financial update memo for a hospital system, covering revenue, costs, and margin trends"),
        ("retail",       "annual financial review for a regional retail chain, written as a concise management summary"),
        ("manufacturing","mid-year financial performance update for a manufacturer, covering EBITDA, capex, and working capital"),
        ("energy",       "quarterly results summary for an oil & gas company, written as a brief for the board"),
        ("technology",   "SaaS company quarterly revenue and ARR update, written as an internal financial summary"),
        ("real_estate",  "quarterly NOI and occupancy report for a commercial real estate portfolio"),
        ("finance",      "private equity portfolio company financial update, covering revenue, burn rate, and runway"),
    ],
    "press_release": [
        ("technology",   "short product launch announcement for a new B2B software platform"),
        ("healthcare",   "hospital system announcing a new partnership or facility expansion"),
        ("retail",       "retail company announcing store openings or a new CEO appointment"),
        ("finance",      "private equity firm announcing a portfolio company acquisition"),
        ("manufacturing","manufacturer announcing a new production facility or capacity expansion"),
        ("energy",       "energy company announcing a renewable project or asset sale"),
        ("real_estate",  "real estate developer announcing a new commercial development"),
        ("technology",   "startup announcing a Series B funding round"),
    ],
    "internal_memo": [
        ("technology",   "new employee onboarding guide for a software company, with week-by-week checklist"),
        ("finance",      "CFO memo to leadership about cost reduction initiative and budget freeze"),
        ("healthcare",   "hospital operations memo about staffing policy changes and shift scheduling"),
        ("retail",       "store manager memo about holiday season procedures and inventory controls"),
        ("manufacturing","plant safety memo about updated protocols and incident reporting"),
        ("technology",   "product team memo about roadmap reprioritization for the next quarter"),
        ("finance",      "HR memo about updated expense reimbursement policy"),
        ("healthcare",   "clinical staff memo about new EHR system rollout and training schedule"),
        ("technology",   "engineering memo about migration to a new cloud infrastructure"),
        ("retail",       "operations memo about new returns policy and customer service procedures"),
    ],
    "contract": [
        ("technology",   "SaaS master services agreement between a software vendor and enterprise client"),
        ("healthcare",   "staffing agency agreement for temporary clinical staff at a hospital"),
        ("real_estate",  "commercial office lease agreement between landlord and corporate tenant"),
        ("manufacturing","supply agreement between a manufacturer and a tier-1 supplier"),
        ("finance",      "investment advisory agreement between a fund manager and institutional client"),
        ("technology",   "software development and maintenance contract"),
        ("retail",       "retail distribution and exclusivity agreement between brand and retailer"),
        ("energy",       "power purchase agreement between a utility and renewable energy developer"),
    ],
    "research_report": [
        ("technology",   "equity research note on a cloud software company, covering growth outlook and valuation"),
        ("healthcare",   "market research report on the telehealth sector, covering adoption trends and competitive landscape"),
        ("retail",       "consumer research report on e-commerce trends and shifting purchase behaviors"),
        ("finance",      "credit research report on a leveraged buyout, covering debt structure and coverage ratios"),
        ("energy",       "sector research report on the energy transition and renewable investment outlook"),
        ("manufacturing","supply chain research report on nearshoring trends and manufacturing cost shifts"),
        ("real_estate",  "commercial real estate market report on office vacancy and hybrid work impact"),
        ("technology",   "market sizing report for the cybersecurity software market"),
    ],
    "regulatory_filing": [
        ("finance",      "SEC proxy statement (DEF 14A) for an annual shareholder meeting of a mid-cap company"),
        ("healthcare",   "FDA 510(k) premarket notification summary for a medical device"),
        ("energy",       "FERC rate filing for a natural gas pipeline operator"),
        ("finance",      "SEC S-1 registration statement for a technology company IPO"),
        ("manufacturing","EPA environmental compliance and emissions report for a manufacturing facility"),
        ("finance",      "SEC 8-K current report disclosing a material event — executive departure"),
        ("healthcare",   "CMS Medicare cost report for a hospital"),
        ("technology",   "FTC HSR premerger notification filing summary"),
    ],
}

SYSTEM_PROMPT = """\
You are generating realistic synthetic business documents for a training dataset for a \
document intelligence system used by consulting firms.

Write a realistic, detailed document of the specified type. Use plausible company names, \
numbers, dates, and industry-specific language. The document should be 350-600 words. \
Write in the style a consultant would actually receive from a client — concise, \
plaintext-friendly, with clear headings. NOT in SEC filing format (no "Item 1.", \
no EDGAR boilerplate, no legal disclaimers unless the doc type calls for it). \
Output ONLY the document text — no titles, no labels, no commentary.\
"""


async def generate_one(client, semaphore, doc_type, industry, description, idx, total):
    async with semaphore:
        try:
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                temperature=0.9,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content":
                    f"Write a {doc_type.replace('_', ' ')}: {description}."}],
            )
            text = response.content[0].text.strip()
            if len(text) < 200:
                print(f"  [{idx}/{total}] too short, skipping")
                return None
            print(f"  [{idx}/{total}] OK  {doc_type:<20} {industry}")
            return {"text": text, "doc_type_label": doc_type, "industry_label": industry}
        except Exception as e:
            print(f"  [{idx}/{total}] ERROR: {e}")
            return None


async def run(workers: int):
    # Step 1: Load and rebalance existing records
    print("Loading existing records…")
    lines = JSONL.read_text(encoding="utf-8").splitlines()
    records = [json.loads(l) for l in lines if l.strip()]
    print(f"  Loaded {len(records)} records")

    # Cap financial_reports across ALL sources (EDGAR + synthetic both over-represented)
    financial = [r for r in records if r["doc_type_label"] == "financial_report"]
    non_financial = [r for r in records if r["doc_type_label"] != "financial_report"]

    random.shuffle(financial)
    financial_capped = financial[:MAX_EDGAR_FINANCIAL]

    kept = financial_capped + non_financial
    print(f"  After capping all financial_reports to {MAX_EDGAR_FINANCIAL}: {len(kept)} records")

    # Step 2: Add sample docs
    print("\nAdding sample docs…")
    sample_records = []
    for fname, (doc_type, industry) in SAMPLE_LABELS.items():
        fpath = SAMPLES_DIR / fname
        if fpath.exists():
            text = fpath.read_text(encoding="utf-8").strip()
            sample_records.append({"text": text, "doc_type_label": doc_type, "industry_label": industry})
            print(f"  Added {fname} -> {doc_type} / {industry}")
        else:
            print(f"  MISSING: {fpath}")

    kept += sample_records

    # Step 3: Figure out how many consulting-style docs to generate per type
    current_counts = Counter(r["doc_type_label"] for r in kept)
    print("\nCurrent doc_type counts after rebalance:")
    for k, v in sorted(current_counts.items()):
        print(f"  {v:>5}  {k}")

    work = []
    for doc_type, templates in CONSULTING_TEMPLATES.items():
        have = current_counts.get(doc_type, 0)
        need = max(0, TARGET_PER_DOC_TYPE - have)
        if need == 0:
            print(f"  {doc_type}: already at target")
            continue
        for i in range(need):
            industry, description = templates[i % len(templates)]
            work.append((doc_type, industry, description))

    random.shuffle(work)
    total = len(work)
    print(f"\nGenerating {total} consulting-style documents…")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    semaphore = asyncio.Semaphore(workers)

    tasks = [
        generate_one(client, semaphore, doc_type, industry, desc, i + 1, total)
        for i, (doc_type, industry, desc) in enumerate(work)
    ]
    results = await asyncio.gather(*tasks)
    new_records = [r for r in results if r is not None]
    print(f"  Generated {len(new_records)} records")

    # Step 4: Write new jsonl
    final = kept + new_records
    random.shuffle(final)
    print(f"\nWriting {len(final)} records to {JSONL}…")
    with open(JSONL, "w", encoding="utf-8") as f:
        for r in final:
            f.write(json.dumps(r) + "\n")

    print("\nFinal doc_type distribution:")
    for k, v in Counter(r["doc_type_label"] for r in final).most_common():
        print(f"  {v:>5}  {k}")

    print("\nFinal industry distribution:")
    for k, v in Counter(r["industry_label"] for r in final).most_common():
        print(f"  {v:>5}  {k}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(run(args.workers))


if __name__ == "__main__":
    main()
