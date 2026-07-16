"""
Unified training data pipeline.

Steps (all in one run):
  1. Cap financial_report docs to MAX_FINANCIAL (prevents class imbalance)
  2. Add labeled sample docs from documents/samples/
  3. Generate consulting-style docs to bring each doc_type to DOC_TYPE_TARGET
  4. Generate industry-diverse docs to bring each industry to INDUSTRY_TARGET
  5. Write final labeled_samples.jsonl and retrain classifiers

Usage:
    PYTHONPATH=. .venv/Scripts/python data/generate_training.py --workers 20
    PYTHONPATH=. .venv/Scripts/python data/generate_training.py --workers 20 --skip-retrain
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

MAX_FINANCIAL = 150
DOC_TYPE_TARGET = 300
INDUSTRY_TARGET = 200

SAMPLE_LABELS = {
    "acme_q3_financials.txt":           ("financial_report",  "finance"),
    "horizon_market_research.txt":      ("research_report",   "technology"),
    "medicore_merger_announcement.txt": ("press_release",     "healthcare"),
    "onboarding_guide.txt":             ("internal_memo",     "technology"),
    "pharma_pipeline_update.txt":       ("research_report",   "healthcare"),
    "retail_q3_update.txt":             ("research_report",   "retail"),
    "pharma_supply_memo.txt":           ("internal_memo",     "healthcare"),
    "tech_saas_board_deck.txt":         ("financial_report",  "technology"),
    "hospital_ops_report.txt":          ("research_report",   "healthcare"),
    "manufacturing_coo_memo.txt":       ("internal_memo",     "manufacturing"),
    "pe_portfolio_review.txt":          ("financial_report",  "finance"),
    "energy_ops_briefing.txt":          ("internal_memo",     "energy"),
    "realestate_asset_review.txt":      ("financial_report",  "real_estate"),
    "consulting_firm_memo.txt":         ("internal_memo",     "professional_services"),
    "logistics_quarterly_report.txt":   ("research_report",   "transportation"),
}

# Doc-type generation templates (consulting style, NOT SEC format)
DOC_TYPE_TEMPLATES = {
    "financial_report": [
        ("finance",         "Q3 earnings summary for a mid-size consumer goods company, written as a short internal brief for management"),
        ("healthcare",      "quarterly financial update memo for a hospital system, covering revenue, costs, and margin trends"),
        ("retail",          "annual financial review for a regional retail chain, written as a concise management summary"),
        ("manufacturing",   "mid-year financial performance update for a manufacturer, covering EBITDA, capex, and working capital"),
        ("energy",          "quarterly results summary for an oil & gas company, written as a brief for the board"),
        ("technology",      "SaaS company quarterly revenue and ARR update, written as an internal financial summary"),
        ("real_estate",     "quarterly NOI and occupancy report for a commercial real estate portfolio"),
        ("finance",         "private equity portfolio company financial update, covering revenue, burn rate, and runway"),
    ],
    "press_release": [
        ("technology",      "short product launch announcement for a new B2B software platform"),
        ("healthcare",      "hospital system announcing a new partnership or facility expansion"),
        ("retail",          "retail company announcing store openings or a new CEO appointment"),
        ("finance",         "private equity firm announcing a portfolio company acquisition"),
        ("manufacturing",   "manufacturer announcing a new production facility or capacity expansion"),
        ("energy",          "energy company announcing a renewable project or asset sale"),
        ("real_estate",     "real estate developer announcing a new commercial development"),
        ("technology",      "startup announcing a Series B funding round"),
    ],
    "internal_memo": [
        ("technology",      "new employee onboarding guide for a software company, with week-by-week checklist"),
        ("finance",         "CFO memo to leadership about cost reduction initiative and budget freeze"),
        ("healthcare",      "hospital operations memo about staffing policy changes and shift scheduling"),
        ("retail",          "store manager memo about holiday season procedures and inventory controls"),
        ("manufacturing",   "plant safety memo about updated protocols and incident reporting"),
        ("technology",      "product team memo about roadmap reprioritization for the next quarter"),
        ("finance",         "HR memo about updated expense reimbursement policy"),
        ("healthcare",      "clinical staff memo about new EHR system rollout and training schedule"),
        ("technology",      "engineering memo about migration to a new cloud infrastructure"),
        ("retail",          "operations memo about new returns policy and customer service procedures"),
    ],
    "contract": [
        ("technology",      "SaaS master services agreement between a software vendor and enterprise client"),
        ("healthcare",      "staffing agency agreement for temporary clinical staff at a hospital"),
        ("real_estate",     "commercial office lease agreement between landlord and corporate tenant"),
        ("manufacturing",   "supply agreement between a manufacturer and a tier-1 supplier"),
        ("finance",         "investment advisory agreement between a fund manager and institutional client"),
        ("technology",      "software development and maintenance contract"),
        ("retail",          "retail distribution and exclusivity agreement between brand and retailer"),
        ("energy",          "power purchase agreement between a utility and renewable energy developer"),
    ],
    "research_report": [
        ("technology",      "equity research note on a cloud software company, covering growth outlook and valuation"),
        ("healthcare",      "market research report on the telehealth sector, covering adoption trends and competitive landscape"),
        ("retail",          "consumer research report on e-commerce trends and shifting purchase behaviors"),
        ("finance",         "credit research report on a leveraged buyout, covering debt structure and coverage ratios"),
        ("energy",          "sector research report on the energy transition and renewable investment outlook"),
        ("manufacturing",   "supply chain research report on nearshoring trends and manufacturing cost shifts"),
        ("real_estate",     "commercial real estate market report on office vacancy and hybrid work impact"),
        ("technology",      "market sizing report for the cybersecurity software market"),
    ],
    "regulatory_filing": [
        ("finance",         "SEC proxy statement for an annual shareholder meeting of a mid-cap company"),
        ("healthcare",      "FDA 510(k) premarket notification summary for a medical device"),
        ("energy",          "FERC rate filing for a natural gas pipeline operator"),
        ("finance",         "SEC S-1 registration statement for a technology company IPO"),
        ("manufacturing",   "EPA environmental compliance and emissions report for a manufacturing facility"),
        ("finance",         "SEC 8-K current report disclosing a material event — executive departure"),
        ("healthcare",      "CMS Medicare cost report for a hospital"),
        ("technology",      "FTC HSR premerger notification filing summary"),
    ],
}

# Industry generation templates — one entry per (doc_type, description)
INDUSTRY_TEMPLATES = {
    "transportation": [
        ("financial_report",  "quarterly earnings report for a major airline"),
        ("financial_report",  "annual report for a freight rail company"),
        ("press_release",     "shipping company announcing new port expansion"),
        ("press_release",     "airline announcing new route network and capacity plans"),
        ("internal_memo",     "logistics company memo about fleet maintenance schedule"),
        ("internal_memo",     "trucking company memo about driver safety protocols"),
        ("contract",          "freight transportation services agreement between shipper and carrier"),
        ("research_report",   "equity research report on the airline industry outlook"),
        ("regulatory_filing", "DOT safety compliance report for a commercial carrier"),
        ("research_report",   "logistics sector research report on last-mile delivery trends"),
    ],
    "media": [
        ("financial_report",  "quarterly earnings report for a streaming media company"),
        ("financial_report",  "annual report for a publishing and digital media group"),
        ("press_release",     "media company announcing content licensing deal or acquisition"),
        ("press_release",     "broadcaster announcing new programming slate"),
        ("internal_memo",     "editorial memo about content strategy and audience growth"),
        ("internal_memo",     "media company memo about advertising revenue targets"),
        ("contract",          "content licensing and distribution agreement"),
        ("contract",          "advertising services agreement between brand and media company"),
        ("research_report",   "media industry research report on streaming vs linear TV trends"),
        ("regulatory_filing", "FCC broadcast license renewal application"),
    ],
    "professional_services": [
        ("financial_report",  "annual report for a management consulting firm"),
        ("financial_report",  "quarterly results for a global accounting and audit firm"),
        ("press_release",     "consulting firm announcing major client engagement or expansion"),
        ("press_release",     "law firm announcing merger or new practice area launch"),
        ("internal_memo",     "consulting firm memo about billable hour targets and utilization"),
        ("internal_memo",     "professional services firm memo about hiring and talent strategy"),
        ("contract",          "management consulting services agreement"),
        ("contract",          "legal services retainer agreement"),
        ("research_report",   "professional services sector report on consulting market trends"),
        ("research_report",   "legal industry research report on law firm profitability"),
    ],
    "education": [
        ("financial_report",  "annual financial report for a private university"),
        ("financial_report",  "quarterly results for an ed-tech company"),
        ("press_release",     "university announcing major research grant or endowment"),
        ("press_release",     "ed-tech company announcing new platform or enrollment milestone"),
        ("internal_memo",     "university administration memo about budget cuts and program changes"),
        ("internal_memo",     "school district memo about curriculum updates and teacher hiring"),
        ("contract",          "educational technology licensing agreement with school district"),
        ("contract",          "research collaboration agreement between university and corporation"),
        ("research_report",   "higher education market research report on enrollment trends"),
        ("regulatory_filing", "Department of Education Title IV compliance report"),
    ],
    "agriculture": [
        ("financial_report",  "annual report for an agricultural commodities company"),
        ("financial_report",  "quarterly results for a crop science and seeds company"),
        ("press_release",     "agribusiness announcing crop yield forecasts or acquisitions"),
        ("press_release",     "food and agriculture company announcing sustainability initiative"),
        ("internal_memo",     "farm operations memo about planting season schedule and inputs"),
        ("internal_memo",     "agribusiness memo about supply chain and commodity price risk"),
        ("contract",          "grain purchase and supply agreement between farmer and processor"),
        ("contract",          "agricultural land lease agreement"),
        ("research_report",   "agricultural commodities research report on crop price outlook"),
        ("research_report",   "agtech sector research report on precision farming adoption"),
    ],
    "telecommunications": [
        ("financial_report",  "annual report for a regional wireless carrier"),
        ("financial_report",  "quarterly results for a broadband internet provider"),
        ("press_release",     "telecom company announcing 5G network expansion"),
        ("internal_memo",     "telecom company memo about network upgrade rollout schedule"),
        ("internal_memo",     "ISP memo about customer churn reduction initiatives"),
        ("contract",          "wholesale network services agreement between carriers"),
        ("research_report",   "telecom research report on broadband infrastructure investment"),
        ("research_report",   "wireless sector report on spectrum auction outcomes"),
    ],
    "defense": [
        ("financial_report",  "annual report for a defense electronics contractor"),
        ("financial_report",  "quarterly results for an aerospace and defense systems company"),
        ("press_release",     "defense contractor announcing new government contract award"),
        ("internal_memo",     "defense contractor memo about program cost overruns"),
        ("internal_memo",     "defense company memo about security clearance compliance"),
        ("contract",          "government defense systems development and integration contract"),
        ("research_report",   "defense sector research report on military spending outlook"),
        ("regulatory_filing", "ITAR export compliance report for defense manufacturer"),
    ],
    "insurance": [
        ("financial_report",  "annual report for a property and casualty insurer"),
        ("financial_report",  "quarterly results for a life insurance company"),
        ("press_release",     "insurance company announcing new product or rate changes"),
        ("internal_memo",     "insurance company memo about claims reserve adjustments"),
        ("internal_memo",     "insurer memo about underwriting guideline updates"),
        ("contract",          "commercial general liability insurance policy agreement"),
        ("research_report",   "insurance industry research report on underwriting trends"),
        ("research_report",   "reinsurance market report on catastrophe loss trends"),
    ],
    "automotive": [
        ("financial_report",  "annual report for an auto manufacturer"),
        ("financial_report",  "quarterly results for an auto parts supplier"),
        ("press_release",     "automaker announcing new EV model or factory investment"),
        ("press_release",     "auto supplier announcing new contract win"),
        ("internal_memo",     "automotive company memo about supply chain disruption response"),
        ("internal_memo",     "automaker memo about production schedule changes"),
        ("contract",          "auto parts supply agreement between OEM and tier-1 supplier"),
        ("research_report",   "automotive sector research report on EV adoption trends"),
    ],
    "construction": [
        ("financial_report",  "annual report for a construction and engineering company"),
        ("financial_report",  "quarterly results for a homebuilder"),
        ("press_release",     "construction company announcing major infrastructure project win"),
        ("press_release",     "homebuilder announcing new community development"),
        ("internal_memo",     "construction company memo about project safety and cost controls"),
        ("internal_memo",     "contractor memo about labor shortage and subcontractor issues"),
        ("contract",          "general contractor construction services agreement"),
        ("research_report",   "construction sector research report on housing market outlook"),
    ],
    "hospitality": [
        ("financial_report",  "annual report for a hotel and resort chain"),
        ("financial_report",  "quarterly results for a restaurant and food service company"),
        ("press_release",     "hospitality company announcing new property openings"),
        ("press_release",     "restaurant group announcing new concept or expansion"),
        ("internal_memo",     "hotel chain memo about occupancy targets and pricing strategy"),
        ("internal_memo",     "restaurant company memo about food cost and staffing"),
        ("contract",          "hotel management agreement between owner and operator"),
        ("research_report",   "hospitality sector research report on travel demand trends"),
    ],
    "mining": [
        ("financial_report",  "annual report for a gold or copper mining company"),
        ("financial_report",  "quarterly results for a lithium mining company"),
        ("press_release",     "mining company announcing new resource discovery or production update"),
        ("press_release",     "miner announcing joint venture or asset sale"),
        ("internal_memo",     "mining company memo about environmental compliance and safety"),
        ("internal_memo",     "mine operations memo about production shortfall"),
        ("contract",          "mining royalty and streaming agreement"),
        ("research_report",   "mining sector research report on critical minerals supply outlook"),
    ],
    "utilities": [
        ("financial_report",  "annual report for an electric utility company"),
        ("financial_report",  "quarterly results for a water and wastewater utility"),
        ("press_release",     "utility company announcing renewable energy transition plan"),
        ("press_release",     "utility announcing rate case filing outcome"),
        ("internal_memo",     "utility memo about grid reliability and capital expenditure plan"),
        ("internal_memo",     "utility memo about wildfire mitigation program"),
        ("contract",          "power purchase agreement between utility and renewable developer"),
        ("research_report",   "utilities sector research report on grid modernization investment"),
    ],
    "consumer_services": [
        ("financial_report",  "annual report for a staffing and outsourcing company"),
        ("financial_report",  "quarterly results for a personal care services chain"),
        ("press_release",     "consumer services company announcing new market expansion"),
        ("press_release",     "services company announcing franchise growth milestone"),
        ("internal_memo",     "services company memo about customer satisfaction targets"),
        ("internal_memo",     "staffing firm memo about recruiter productivity goals"),
        ("contract",          "outsourced services agreement between company and vendor"),
        ("research_report",   "consumer services sector research report on gig economy trends"),
    ],
    "consumer_goods": [
        ("financial_report",  "annual report for a consumer packaged goods company"),
        ("financial_report",  "quarterly results for a household products manufacturer"),
        ("press_release",     "CPG company announcing new product launch or brand acquisition"),
        ("press_release",     "consumer goods company announcing sustainability initiative"),
        ("internal_memo",     "consumer goods company memo about pricing and promotional strategy"),
        ("internal_memo",     "CPG memo about supply chain and raw material cost pressures"),
        ("contract",          "retail distribution agreement between CPG brand and retailer"),
        ("research_report",   "consumer goods sector research report on private label trends"),
    ],
    "aerospace_defense": [
        ("financial_report",  "annual report for a commercial aerospace manufacturer"),
        ("financial_report",  "quarterly results for a satellite and space systems company"),
        ("press_release",     "aerospace company announcing new aircraft order or launch contract"),
        ("press_release",     "space company announcing new satellite constellation milestone"),
        ("internal_memo",     "aerospace company memo about supply chain and certification delays"),
        ("internal_memo",     "aerospace memo about production rate ramp challenges"),
        ("contract",          "aircraft maintenance and overhaul services agreement"),
        ("research_report",   "aerospace sector research report on commercial aviation recovery"),
    ],
    # Boost under-represented core industries too
    "retail": [
        ("financial_report",  "quarterly results for a specialty retail chain with margin pressure"),
        ("internal_memo",     "retail operations memo about inventory management and shrink"),
        ("research_report",   "retail sector report on omnichannel strategy and store economics"),
        ("press_release",     "retailer announcing store optimization and new format rollout"),
        ("contract",          "retail real estate lease and co-tenancy agreement"),
    ],
    "energy": [
        ("financial_report",  "quarterly results for a midstream pipeline operator"),
        ("internal_memo",     "energy company memo about safety incident and regulatory response"),
        ("research_report",   "energy sector report on LNG export market and pricing"),
        ("press_release",     "E&P company announcing new drilling program and reserves update"),
        ("contract",          "gas gathering and processing agreement between producer and midstream"),
    ],
    "manufacturing": [
        ("financial_report",  "quarterly results for a specialty chemicals manufacturer"),
        ("internal_memo",     "manufacturing operations memo about capacity utilization and downtime"),
        ("research_report",   "manufacturing sector report on reshoring trends and labor costs"),
        ("press_release",     "manufacturer announcing new plant investment or product line"),
        ("contract",          "manufacturing services agreement between OEM and contract manufacturer"),
    ],
    "technology": [
        ("financial_report",  "SaaS company annual results with ARR, churn, and burn analysis"),
        ("internal_memo",     "engineering memo about technical debt and platform migration"),
        ("research_report",   "technology sector report on AI infrastructure spending"),
        ("press_release",     "tech company announcing strategic partnership or platform launch"),
        ("contract",          "enterprise software license and professional services agreement"),
    ],
    "healthcare": [
        ("financial_report",  "quarterly results for a specialty pharmacy benefits manager"),
        ("internal_memo",     "healthcare system memo about payer contract renegotiation"),
        ("research_report",   "healthcare sector report on value-based care adoption"),
        ("press_release",     "health system announcing new service line or facility"),
        ("contract",          "physician employment agreement between health system and doctor group"),
    ],
    "finance": [
        ("financial_report",  "PE fund quarterly portfolio update with MOIC and IRR by company"),
        ("internal_memo",     "investment bank memo about deal pipeline and headcount"),
        ("research_report",   "credit market report on leveraged loan spreads and default rates"),
        ("press_release",     "asset manager announcing new fund close or AUM milestone"),
        ("contract",          "limited partnership agreement for a private equity fund"),
    ],
    "real_estate": [
        ("financial_report",  "REIT quarterly results with FFO, occupancy, and same-store NOI"),
        ("internal_memo",     "real estate developer memo about construction cost overruns"),
        ("research_report",   "commercial real estate report on industrial demand and cap rates"),
        ("press_release",     "developer announcing new mixed-use project groundbreaking"),
        ("contract",          "purchase and sale agreement for commercial property acquisition"),
    ],
}

SYSTEM_PROMPT = """\
You are generating realistic synthetic business documents for a training dataset for a \
document intelligence system used by consulting firms.

Write a realistic, detailed document of the specified type. Use plausible company names, \
numbers, dates, and industry-specific language. The document should be 350-600 words. \
Write in the style a consultant would actually receive from a client — concise, \
plaintext-friendly, with clear headings. NOT in SEC filing format. \
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
                    f"Write a {doc_type.replace('_', ' ')}: {description}. Industry: {industry}."}],
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


async def run(workers: int, skip_retrain: bool):
    # ── Step 1: Load existing records ─────────────────────────────────────────
    print("Loading existing records…")
    lines = JSONL.read_text(encoding="utf-8").splitlines()
    records = [json.loads(l) for l in lines if l.strip()]
    print(f"  Loaded {len(records)} records")

    # ── Step 2: Cap financial_reports ────────────────────────────────────────
    financial = [r for r in records if r["doc_type_label"] == "financial_report"]
    non_financial = [r for r in records if r["doc_type_label"] != "financial_report"]
    random.shuffle(financial)
    kept = financial[:MAX_FINANCIAL] + non_financial
    print(f"  After capping financial_reports to {MAX_FINANCIAL}: {len(kept)} records")

    # ── Step 3: Add sample docs ───────────────────────────────────────────────
    print("\nAdding sample docs…")
    existing_texts = {r["text"][:100] for r in kept}
    for fname, (doc_type, industry) in SAMPLE_LABELS.items():
        fpath = SAMPLES_DIR / fname
        if not fpath.exists():
            print(f"  MISSING: {fname}")
            continue
        text = fpath.read_text(encoding="utf-8").strip()
        if text[:100] in existing_texts:
            print(f"  Already present: {fname}")
            continue
        kept.append({"text": text, "doc_type_label": doc_type, "industry_label": industry})
        print(f"  Added {fname} -> {doc_type} / {industry}")

    # ── Step 4: Build work list ───────────────────────────────────────────────
    doc_type_counts = Counter(r["doc_type_label"] for r in kept)
    industry_counts = Counter(r["industry_label"] for r in kept)

    print("\nCurrent doc_type counts:")
    for k, v in sorted(doc_type_counts.items()): print(f"  {v:>5}  {k}")
    print("\nCurrent industry counts:")
    for k, v in sorted(industry_counts.items()): print(f"  {v:>5}  {k}")

    work = []  # (doc_type, industry, description)

    # Doc type top-up
    for doc_type, templates in DOC_TYPE_TEMPLATES.items():
        have = doc_type_counts.get(doc_type, 0)
        need = max(0, DOC_TYPE_TARGET - have)
        for i in range(need):
            industry, desc = templates[i % len(templates)]
            work.append((doc_type, industry, desc))

    # Industry top-up (only for industries with templates defined)
    for industry, templates in INDUSTRY_TEMPLATES.items():
        have = industry_counts.get(industry, 0)
        need = max(0, INDUSTRY_TARGET - have)
        for i in range(need):
            doc_type, desc = templates[i % len(templates)]
            work.append((doc_type, industry, desc))

    # Deduplicate (same triple generated by both loops)
    work = list({(dt, ind, d) for dt, ind, d in work})
    random.shuffle(work)
    total = len(work)
    print(f"\nGenerating {total} documents ({DOC_TYPE_TARGET} per doc_type, {INDUSTRY_TARGET} per industry)…")

    # ── Step 5: Generate ─────────────────────────────────────────────────────
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    semaphore = asyncio.Semaphore(workers)
    tasks = [
        generate_one(client, semaphore, dt, ind, desc, i + 1, total)
        for i, (dt, ind, desc) in enumerate(work)
    ]
    results = await asyncio.gather(*tasks)
    new_records = [r for r in results if r is not None]
    print(f"\n  Generated {len(new_records)} new records")

    # ── Step 6: Write final JSONL ─────────────────────────────────────────────
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

    # ── Step 7: Retrain ───────────────────────────────────────────────────────
    if not skip_retrain:
        print("\nRetraining classifiers…")
        import subprocess, sys
        subprocess.run([sys.executable, "train.py"], check=True)
    else:
        print("\nSkipped retrain (--skip-retrain). Run: python train.py")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--skip-retrain", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.workers, args.skip_retrain))


if __name__ == "__main__":
    main()
