"""Quick test of LLM-based pain point extraction."""
import sys
sys.path.insert(0, ".")

from app.pipelines.pain_points import detect_pain_points

DOCS = [
    ("technology", "documents/samples/tech_saas_board_deck.txt", "saas"),
    ("healthcare", "documents/samples/pharma_supply_memo.txt", "pharma"),
    ("retail", "documents/samples/retail_q3_update.txt", "retail"),
    ("finance", "documents/samples/pe_portfolio_review.txt", "pe"),
]

for industry, path, label in DOCS:
    text = open(path).read()
    results = detect_pain_points(text, industry)
    print(f"\n=== {label} ({len(results)} pain points) ===")
    for r in results:
        print(f"  - {r['label']}")
