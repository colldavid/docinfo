"""
Generate synthetic training docs to balance under-represented industry labels.
Appends to data/edgar/labeled_samples.jsonl.

Target ~150 docs per industry. Run after checking current counts.

Usage:
    PYTHONPATH=. .venv/Scripts/python data/generate_industry_training.py --workers 20
"""
import argparse
import asyncio
import json
import random
from pathlib import Path
from collections import Counter

import anthropic
from app.config import settings

OUTPUT = Path(__file__).parent / "edgar" / "labeled_samples.jsonl"

# (industry_label, doc_type_label, description)
# We target industries that are under-represented after EDGAR restore.
# Each tuple generates one doc. We'll replicate to hit target count.
TEMPLATES = {
    "transportation": [
        ("financial_report",   "quarterly earnings report for a major airline"),
        ("financial_report",   "annual report for a freight rail company"),
        ("press_release",      "shipping company announcing new port expansion"),
        ("press_release",      "airline announcing new route network and capacity plans"),
        ("internal_memo",      "logistics company memo about fleet maintenance schedule"),
        ("internal_memo",      "trucking company memo about driver safety protocols"),
        ("contract",           "freight transportation services agreement between shipper and carrier"),
        ("research_report",    "equity research report on the airline industry outlook"),
        ("regulatory_filing",  "DOT safety compliance report for a commercial carrier"),
        ("research_report",    "logistics sector research report on last-mile delivery trends"),
    ],
    "media": [
        ("financial_report",   "quarterly earnings report for a streaming media company"),
        ("financial_report",   "annual report for a publishing and digital media group"),
        ("press_release",      "media company announcing content licensing deal or acquisition"),
        ("press_release",      "broadcaster announcing new programming slate"),
        ("internal_memo",      "editorial memo about content strategy and audience growth"),
        ("internal_memo",      "media company memo about advertising revenue targets"),
        ("contract",           "content licensing and distribution agreement"),
        ("contract",           "advertising services agreement between brand and media company"),
        ("research_report",    "media industry research report on streaming vs linear TV trends"),
        ("regulatory_filing",  "FCC broadcast license renewal application"),
    ],
    "professional_services": [
        ("financial_report",   "annual report for a management consulting firm"),
        ("financial_report",   "quarterly results for a global accounting and audit firm"),
        ("press_release",      "consulting firm announcing major client engagement or expansion"),
        ("press_release",      "law firm announcing merger or new practice area launch"),
        ("internal_memo",      "consulting firm memo about billable hour targets and utilization"),
        ("internal_memo",      "professional services firm memo about hiring and talent strategy"),
        ("contract",           "management consulting services agreement"),
        ("contract",           "legal services retainer agreement"),
        ("research_report",    "professional services sector report on consulting market trends"),
        ("research_report",    "legal industry research report on law firm profitability"),
    ],
    "education": [
        ("financial_report",   "annual financial report for a private university"),
        ("financial_report",   "quarterly results for an ed-tech company"),
        ("press_release",      "university announcing major research grant or endowment"),
        ("press_release",      "ed-tech company announcing new platform or enrollment milestone"),
        ("internal_memo",      "university administration memo about budget cuts and program changes"),
        ("internal_memo",      "school district memo about curriculum updates and teacher hiring"),
        ("contract",           "educational technology licensing agreement with school district"),
        ("contract",           "research collaboration agreement between university and corporation"),
        ("research_report",    "higher education market research report on enrollment trends"),
        ("regulatory_filing",  "Department of Education Title IV compliance report"),
    ],
    "agriculture": [
        ("financial_report",   "annual report for an agricultural commodities company"),
        ("financial_report",   "quarterly results for a crop science and seeds company"),
        ("press_release",      "agribusiness announcing crop yield forecasts or acquisitions"),
        ("press_release",      "food and agriculture company announcing sustainability initiative"),
        ("internal_memo",      "farm operations memo about planting season schedule and inputs"),
        ("internal_memo",      "agribusiness memo about supply chain and commodity price risk"),
        ("contract",           "grain purchase and supply agreement between farmer and processor"),
        ("contract",           "agricultural land lease agreement"),
        ("research_report",    "agricultural commodities research report on crop price outlook"),
        ("research_report",    "agtech sector research report on precision farming adoption"),
    ],
    # Also boost these to ~150
    "telecommunications": [
        ("financial_report",   "annual report for a regional wireless carrier"),
        ("financial_report",   "quarterly results for a broadband internet provider"),
        ("internal_memo",      "telecom company memo about network upgrade rollout schedule"),
        ("internal_memo",      "ISP memo about customer churn reduction initiatives"),
        ("contract",           "wholesale network services agreement between carriers"),
        ("research_report",    "telecom research report on broadband infrastructure investment"),
    ],
    "defense": [
        ("financial_report",   "annual report for a defense electronics contractor"),
        ("financial_report",   "quarterly results for an aerospace and defense systems company"),
        ("internal_memo",      "defense contractor memo about program cost overruns"),
        ("internal_memo",      "defense company memo about security clearance compliance"),
        ("contract",           "government defense systems development and integration contract"),
        ("research_report",    "defense sector research report on military spending outlook"),
        ("regulatory_filing",  "ITAR export compliance report for defense manufacturer"),
    ],
    # Remap existing noisy EDGAR labels by generating clean examples
    "insurance": [
        ("financial_report",   "annual report for a property and casualty insurer"),
        ("financial_report",   "quarterly results for a life insurance company"),
        ("press_release",      "insurance company announcing new product or rate changes"),
        ("internal_memo",      "insurance company memo about claims reserve adjustments"),
        ("contract",           "commercial general liability insurance policy agreement"),
        ("research_report",    "insurance industry research report on underwriting trends"),
    ],
    "automotive": [
        ("financial_report",   "annual report for an auto manufacturer"),
        ("financial_report",   "quarterly results for an auto parts supplier"),
        ("press_release",      "automaker announcing new EV model or factory investment"),
        ("internal_memo",      "automotive company memo about supply chain disruption response"),
        ("contract",           "auto parts supply agreement between OEM and tier-1 supplier"),
        ("research_report",    "automotive sector research report on EV adoption trends"),
    ],
    "construction": [
        ("financial_report",   "annual report for a construction and engineering company"),
        ("financial_report",   "quarterly results for a homebuilder"),
        ("press_release",      "construction company announcing major infrastructure project win"),
        ("internal_memo",      "construction company memo about project safety and cost controls"),
        ("contract",           "general contractor construction services agreement"),
        ("research_report",    "construction sector research report on housing market outlook"),
    ],
    "hospitality": [
        ("financial_report",   "annual report for a hotel and resort chain"),
        ("financial_report",   "quarterly results for a restaurant and food service company"),
        ("press_release",      "hospitality company announcing new property openings"),
        ("internal_memo",      "hotel chain memo about occupancy targets and pricing strategy"),
        ("contract",           "hotel management agreement between owner and operator"),
        ("research_report",    "hospitality sector research report on travel demand trends"),
    ],
    "mining": [
        ("financial_report",   "annual report for a gold or copper mining company"),
        ("financial_report",   "quarterly results for a coal or lithium mining company"),
        ("press_release",      "mining company announcing new resource discovery or production update"),
        ("internal_memo",      "mining company memo about environmental compliance and safety"),
        ("contract",           "mining royalty and streaming agreement"),
        ("research_report",    "mining sector research report on critical minerals supply outlook"),
    ],
    "utilities": [
        ("financial_report",   "annual report for an electric utility company"),
        ("financial_report",   "quarterly results for a water and wastewater utility"),
        ("press_release",      "utility company announcing renewable energy transition plan"),
        ("internal_memo",      "utility memo about grid reliability and capital expenditure plan"),
        ("contract",           "power purchase agreement between utility and renewable developer"),
        ("research_report",    "utilities sector research report on grid modernization investment"),
    ],
    "consumer_services": [
        ("financial_report",   "annual report for a staffing and outsourcing company"),
        ("financial_report",   "quarterly results for a personal care services chain"),
        ("press_release",      "consumer services company announcing new market expansion"),
        ("internal_memo",      "services company memo about customer satisfaction targets"),
        ("contract",           "outsourced services agreement between company and vendor"),
        ("research_report",    "consumer services sector research report on gig economy trends"),
    ],
    "consumer_goods": [
        ("financial_report",   "annual report for a consumer packaged goods company"),
        ("financial_report",   "quarterly results for a household products manufacturer"),
        ("press_release",      "CPG company announcing new product launch or brand acquisition"),
        ("internal_memo",      "consumer goods company memo about pricing and promotional strategy"),
        ("contract",           "retail distribution agreement between CPG brand and retailer"),
        ("research_report",    "consumer goods sector research report on private label trends"),
    ],
    "aerospace_defense": [
        ("financial_report",   "annual report for a commercial aerospace manufacturer"),
        ("financial_report",   "quarterly results for a satellite and space systems company"),
        ("press_release",      "aerospace company announcing new aircraft order or launch contract"),
        ("internal_memo",      "aerospace company memo about supply chain and certification delays"),
        ("contract",           "aircraft maintenance and overhaul services agreement"),
        ("research_report",    "aerospace sector research report on commercial aviation recovery"),
    ],
}

# Target count per industry
TARGET = 150

SYSTEM_PROMPT = """\
You are generating realistic synthetic business documents for a training dataset. \
Write a realistic, detailed document of the specified type. Use plausible company names, \
numbers, dates, and industry-specific language. The document should be 400-700 words. \
Output ONLY the document text — no titles, no labels, no commentary.\
"""


async def generate_one(client, semaphore, industry, doc_type, description, idx, total):
    async with semaphore:
        try:
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                temperature=0.9,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content":
                    f"Write a {doc_type.replace('_', ' ')} document: {description}. Industry: {industry}."}],
            )
            text = response.content[0].text.strip()
            if len(text) < 200:
                print(f"  [{idx}/{total}] too short, skipping")
                return None
            print(f"  [{idx}/{total}] OK  {industry:<22} {doc_type}")
            return {"text": text, "doc_type_label": doc_type, "industry_label": industry}
        except Exception as e:
            print(f"  [{idx}/{total}] ERROR: {e}")
            return None


async def run(workers: int):
    # Load current counts
    lines = OUTPUT.read_text(encoding="utf-8").splitlines()
    records = [json.loads(l) for l in lines if l.strip()]
    current = Counter(r["industry_label"] for r in records)

    print("Current industry counts:")
    for k, v in sorted(current.items()):
        print(f"  {v:>5}  {k}")

    # Build work list: for each industry, generate enough to reach TARGET
    work = []
    for industry, templates in TEMPLATES.items():
        have = current.get(industry, 0)
        need = max(0, TARGET - have)
        if need == 0:
            print(f"  {industry}: already at target, skipping")
            continue
        # cycle through templates to fill need
        for i in range(need):
            doc_type, description = templates[i % len(templates)]
            work.append((industry, doc_type, description))

    random.shuffle(work)
    total = len(work)
    print(f"\nGenerating {total} documents across {len(TEMPLATES)} industries…")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    semaphore = asyncio.Semaphore(workers)

    tasks = [
        generate_one(client, semaphore, industry, doc_type, desc, i + 1, total)
        for i, (industry, doc_type, desc) in enumerate(work)
    ]
    results = await asyncio.gather(*tasks)
    records_new = [r for r in results if r is not None]

    print(f"\nWriting {len(records_new)} records…")
    with open(OUTPUT, "a", encoding="utf-8") as f:
        for r in records_new:
            f.write(json.dumps(r) + "\n")

    counts = Counter(r["industry_label"] for r in records_new)
    print("\nGenerated by industry:")
    for k, v in counts.most_common():
        print(f"  {v:>4}  {k}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(run(args.workers))


if __name__ == "__main__":
    main()
