"""
One-shot top-up for core consulting industries that are under-represented.
Appends directly to labeled_samples.jsonl, then retrains.
"""
import asyncio, json, random
from collections import Counter
from pathlib import Path
import anthropic
from app.config import settings

JSONL = Path(__file__).parent / "edgar" / "labeled_samples.jsonl"
TARGET = 200

TEMPLATES = {
    "retail": [
        ("internal_memo",    "retail ops memo about inventory build-up, markdowns, and vendor issues"),
        ("research_report",  "retail sector report on foot traffic, omnichannel, and margin trends"),
        ("financial_report", "specialty retailer quarterly results with comp sales and margin pressure"),
        ("press_release",    "retailer announcing store closures, new format rollout, or CEO change"),
        ("contract",         "retail real estate lease agreement with co-tenancy and kick-out clauses"),
        ("internal_memo",    "district manager memo about shrink, staffing turnover, and seasonal prep"),
        ("research_report",  "consumer research on private label adoption and brand loyalty shifts"),
        ("financial_report", "off-price retailer annual results with inventory turn and gross margin"),
    ],
    "energy": [
        ("internal_memo",    "midstream pipeline ops memo about throughput shortfall and maintenance"),
        ("research_report",  "energy sector report on LNG pricing, export capacity, and demand outlook"),
        ("financial_report", "E&P company quarterly results with production volumes and hedging"),
        ("press_release",    "energy company announcing new drilling program or asset divestiture"),
        ("contract",         "gas gathering and processing agreement between producer and midstream co"),
        ("internal_memo",    "utility memo about grid reliability, capital plan, and regulatory filing"),
        ("research_report",  "renewable energy report on solar/wind build-out and PPA pricing trends"),
        ("financial_report", "refinery quarterly results with crack spreads and utilization rates"),
    ],
    "real_estate": [
        ("financial_report", "office REIT quarterly results with occupancy, lease spreads, and NOI"),
        ("internal_memo",    "developer memo about construction cost overruns and delivery delays"),
        ("research_report",  "CRE market report on industrial demand, cap rates, and debt availability"),
        ("press_release",    "developer announcing mixed-use groundbreaking or portfolio acquisition"),
        ("contract",         "purchase and sale agreement for commercial property with due diligence"),
        ("internal_memo",    "asset manager memo about lease expirations, tenant retention, and capex"),
        ("research_report",  "multifamily market report on rent growth, vacancy, and new supply"),
        ("financial_report", "industrial REIT results with same-store NOI and development pipeline"),
    ],
    "manufacturing": [
        ("internal_memo",    "plant ops memo about downtime, quality defects, and labor shortages"),
        ("research_report",  "manufacturing report on reshoring trends, input costs, and capacity"),
        ("financial_report", "specialty manufacturer quarterly results with margins and backlog"),
        ("press_release",    "manufacturer announcing new plant investment or product line launch"),
        ("contract",         "contract manufacturing services agreement between OEM and CM"),
        ("internal_memo",    "COO memo about production schedule, supplier delays, and cost controls"),
        ("research_report",  "supply chain report on nearshoring, dual sourcing, and lead times"),
        ("financial_report", "industrial manufacturer annual results with segment EBITDA and capex"),
    ],
    "technology": [
        ("financial_report", "SaaS company quarterly results with ARR, NRR, churn, and burn rate"),
        ("internal_memo",    "engineering memo about technical debt, platform migration, and velocity"),
        ("research_report",  "technology sector report on AI infrastructure spending and vendor landscape"),
        ("press_release",    "tech company announcing strategic partnership, acquisition, or new product"),
        ("contract",         "enterprise SaaS subscription and professional services agreement"),
        ("internal_memo",    "product memo about roadmap reprioritization and customer escalations"),
        ("research_report",  "cybersecurity market report on zero-trust adoption and vendor consolidation"),
        ("financial_report", "semiconductor company results with utilization, ASPs, and inventory"),
    ],
    "healthcare": [
        ("financial_report", "health system quarterly results with volumes, payer mix, and margins"),
        ("internal_memo",    "CFO memo about revenue cycle, denials, and cost reduction targets"),
        ("research_report",  "healthcare sector report on value-based care and payer contract trends"),
        ("press_release",    "health system announcing service line expansion or physician group deal"),
        ("contract",         "physician employment agreement between health system and medical group"),
        ("internal_memo",    "ops memo about nursing vacancies, agency spend, and locum coverage"),
        ("research_report",  "pharma market report on drug pricing, biosimilars, and pipeline value"),
        ("financial_report", "specialty pharmacy results with specialty revenue mix and gross margin"),
    ],
    "finance": [
        ("financial_report", "PE fund quarterly portfolio update with MOIC, IRR, and company snapshots"),
        ("internal_memo",    "investment bank memo about deal pipeline, headcount, and fee outlook"),
        ("research_report",  "credit market report on leveraged loan spreads, defaults, and supply"),
        ("press_release",    "asset manager announcing new fund close, AUM milestone, or acquisition"),
        ("contract",         "limited partnership agreement for a private equity buyout fund"),
        ("internal_memo",    "CFO memo about covenant compliance, liquidity, and debt maturity"),
        ("research_report",  "M&A market report on deal volume, multiples, and sector activity"),
        ("financial_report", "BDC quarterly results with NAV, portfolio yield, and credit quality"),
    ],
}

SYSTEM_PROMPT = """\
You are generating realistic synthetic business documents for a consulting firm training dataset.
Write a realistic document of the specified type. Use plausible names, numbers, and dates.
350-600 words. Consulting style — concise, plaintext-friendly, clear headings. NOT SEC format.
Output ONLY the document text.\
"""

async def generate_one(client, sem, doc_type, industry, desc, idx, total):
    async with sem:
        try:
            r = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024, temperature=0.9,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"Write a {doc_type.replace('_',' ')}: {desc}. Industry: {industry}."}],
            )
            text = r.content[0].text.strip()
            if len(text) < 200:
                return None
            print(f"  [{idx}/{total}] OK  {industry:<18} {doc_type}")
            return {"text": text, "doc_type_label": doc_type, "industry_label": industry}
        except Exception as e:
            print(f"  [{idx}/{total}] ERR {e}")
            return None

async def run():
    lines = JSONL.read_text(encoding="utf-8").splitlines()
    records = [json.loads(l) for l in lines if l.strip()]
    counts = Counter(r["industry_label"] for r in records)

    print("Current core industry counts:")
    for ind in TEMPLATES:
        print(f"  {counts.get(ind, 0):>5}  {ind}")

    work = []
    for industry, templates in TEMPLATES.items():
        need = max(0, TARGET - counts.get(industry, 0))
        for i in range(need):
            doc_type, desc = templates[i % len(templates)]
            work.append((doc_type, industry, desc))

    random.shuffle(work)
    total = len(work)
    print(f"\nGenerating {total} docs to bring core industries to {TARGET}…")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    sem = asyncio.Semaphore(20)
    results = await asyncio.gather(*[
        generate_one(client, sem, dt, ind, desc, i+1, total)
        for i, (dt, ind, desc) in enumerate(work)
    ])
    new = [r for r in results if r]
    print(f"\nGenerated {len(new)} records. Appending…")

    with open(JSONL, "a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r) + "\n")

    final_counts = Counter(r["industry_label"] for r in records + new)
    print("\nUpdated core industry counts:")
    for ind in TEMPLATES:
        print(f"  {final_counts.get(ind, 0):>5}  {ind}")

    print("\nRetraining classifiers…")
    import subprocess, sys
    subprocess.run([sys.executable, "train.py"], check=True)

if __name__ == "__main__":
    asyncio.run(run())
