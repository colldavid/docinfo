"""Show raw classifier probabilities for sample docs."""
import sys
sys.path.insert(0, ".")

from app.pipelines.classifier import classify_document_type, classify_industry

DOCS = [
    "documents/samples/retail_q3_update.txt",
    "documents/samples/tech_saas_board_deck.txt",
    "documents/samples/pharma_supply_memo.txt",
    "documents/samples/hospital_ops_report.txt",
    "documents/samples/manufacturing_coo_memo.txt",
    "documents/samples/pe_portfolio_review.txt",
    "documents/samples/energy_ops_briefing.txt",
    "documents/samples/realestate_asset_review.txt",
    "documents/samples/consulting_firm_memo.txt",
    "documents/samples/logistics_quarterly_report.txt",
]

for path in DOCS:
    text = open(path).read()
    name = path.split("/")[-1].replace(".txt", "")

    dt_label, dt_prob = classify_document_type(text)
    ind_labels, ind_probs = classify_industry(text)

    ind2_label = ind_labels[1] if len(ind_labels) > 1 else "n/a"
    ind2_prob = f"{ind_probs[1]:.3f}" if len(ind_probs) > 1 else "n/a"

    print(f"\n{name}")
    print(f"  doc_type:  {dt_label} ({dt_prob:.3f})")
    print(f"  industry:  {ind_labels[0]} ({ind_probs[0]:.3f})  | 2nd: {ind2_label} ({ind2_prob})")
