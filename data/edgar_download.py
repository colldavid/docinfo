"""
Download SEC EDGAR filings for classifier training.

Usage:
    python data/edgar_download.py

What this does:
  1. Fetches the EDGAR full-text search index to get a representative sample
     of 10-K, 10-Q, and 8-K filings (doc_type labels).
  2. Pulls company metadata (SIC code) for each filing and maps it to our
     industry taxonomy via data/sic_to_industry.json.
  3. Downloads the filing text (HTML stripped to plain text).
  4. Saves labeled records to data/edgar/labeled_samples.jsonl —
     one JSON object per line with keys: text, doc_type_label, industry_label.

EDGAR's full-index files are public domain. No API key required.
Rate limit: ~10 requests/sec sustained is safe; we sleep between requests.

Output format (one record per line in JSONL):
{
  "text": "...",
  "doc_type_label": "financial_report",   # mapped from form type
  "industry_label": "technology"           # mapped from SIC code
}
"""

import json
import time
import logging
import re
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EDGAR_BASE = "https://www.sec.gov"
OUTPUT_DIR = Path(__file__).parent / "edgar"
OUTPUT_FILE = OUTPUT_DIR / "labeled_samples.jsonl"
SIC_MAP_FILE = Path(__file__).parent / "sic_to_industry.json"

# Number of filings to sample per form type
SAMPLES_PER_FORM = 150

# Map EDGAR form types to our doc_type taxonomy
FORM_TO_DOC_TYPE = {
    "10-K": "financial_report",
    "10-Q": "financial_report",
    "8-K": "press_release",
    "DEF 14A": "regulatory_filing",
    "S-1": "regulatory_filing",
    "20-F": "financial_report",
    "6-K": "financial_report",
}

TARGET_FORMS = list(FORM_TO_DOC_TYPE.keys())

HEADERS = {
    "User-Agent": "docinfo-classifier research@example.com",  # EDGAR requires a contact
    "Accept-Encoding": "gzip, deflate",
}

REQUEST_DELAY = 0.15  # seconds between requests (~6-7/sec, well within limits)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        # Handle gzip transparently (urllib does this automatically for gzip)
        return raw.decode("utf-8", errors="replace")


def strip_html(html: str) -> str:
    """Minimal HTML stripping — remove tags and decode common entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#160;", " ")
    text = re.sub(r"\s{3,}", "\n\n", text)
    return text.strip()


def get_quarter_index(year: int, quarter: int) -> list[dict]:
    """
    Download the EDGAR full-index for a given year/quarter.
    Returns a list of dicts with keys: cik, company, form_type, date, filename.
    """
    url = f"{EDGAR_BASE}/Archives/edgar/full-index/{year}/QTR{quarter}/company.idx"
    logger.info(f"Fetching index: {url}")
    try:
        raw = fetch(url)
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        logger.error(f"Failed to fetch index {year}/Q{quarter}: {e}")
        return []

    records = []
    for line in raw.splitlines()[10:]:  # first 10 lines are header
        if len(line) < 100:
            continue
        try:
            company = line[0:62].strip()
            form_type = line[62:74].strip()
            cik = line[74:86].strip()
            date = line[86:98].strip()
            filename = line[98:].strip()
            if form_type in TARGET_FORMS:
                records.append({
                    "cik": cik,
                    "company": company,
                    "form_type": form_type,
                    "date": date,
                    "filename": filename,
                })
        except Exception:
            continue
    return records


def get_sic_for_cik(cik: str) -> str | None:
    """Fetch company facts JSON to get the SIC code."""
    url = f"{EDGAR_BASE}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=1&search_text=&output=atom"
    try:
        raw = fetch(url)
        time.sleep(REQUEST_DELAY)
        match = re.search(r"<assigned-sic>(\d+)</assigned-sic>", raw)
        if match:
            return match.group(1)
    except Exception as e:
        logger.debug(f"SIC fetch failed for CIK {cik}: {e}")
    return None


def get_filing_text(filename: str) -> str | None:
    """Download the primary document for a filing index."""
    index_url = f"{EDGAR_BASE}/Archives/{filename}"
    # filename is like edgar/data/CIK/ACCESSION/ACCESSION-index.htm
    # We need to fetch the index page to find the primary document URL
    try:
        index_html = fetch(index_url)
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        logger.debug(f"Failed to fetch index {index_url}: {e}")
        return None

    # Find the primary document — look for the first .htm/.html/.txt document
    # that isn't the index file itself
    accession_dir = "/".join(index_url.rsplit("/", 1)[:-1])
    match = re.search(
        r'href="([^"]+\.(?:htm|html|txt))"[^>]*>[^<]*(?:10-K|10-Q|8-K|complete|primary)',
        index_html,
        re.IGNORECASE,
    )
    if not match:
        # Fallback: grab the first .htm link that isn't the index
        match = re.search(r'href="(/Archives/edgar/data/[^"]+\.(?:htm|html|txt))"', index_html)

    if not match:
        logger.debug(f"No primary document found in {index_url}")
        return None

    doc_path = match.group(1)
    if not doc_path.startswith("/"):
        doc_path = f"{accession_dir}/{doc_path}"
    doc_url = f"{EDGAR_BASE}{doc_path}"

    try:
        raw = fetch(doc_url)
        time.sleep(REQUEST_DELAY)
        text = strip_html(raw)
        # Truncate to ~8000 chars for training — enough signal, manageable size
        return text[:8000]
    except Exception as e:
        logger.debug(f"Failed to fetch document {doc_url}: {e}")
        return None


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(SIC_MAP_FILE) as f:
        sic_map: dict[str, str] = json.load(f)
        # Remove the _comment key
        sic_map.pop("_comment", None)

    # Collect filings from recent quarters
    all_records: list[dict] = []
    for year in [2023, 2022, 2021]:
        for quarter in [4, 3, 2, 1]:
            records = get_quarter_index(year, quarter)
            all_records.extend(records)
            if len(all_records) >= SAMPLES_PER_FORM * len(TARGET_FORMS) * 5:
                break
        else:
            continue
        break

    logger.info(f"Total candidate filings: {len(all_records)}")

    # Balance: sample up to SAMPLES_PER_FORM per form type
    from collections import defaultdict
    import random

    random.seed(42)
    by_form: dict[str, list[dict]] = defaultdict(list)
    for r in all_records:
        by_form[r["form_type"]].append(r)

    sampled = []
    for form_type, records in by_form.items():
        random.shuffle(records)
        sampled.extend(records[:SAMPLES_PER_FORM])

    logger.info(f"Sampled {len(sampled)} filings to download")

    written = 0
    with open(OUTPUT_FILE, "w") as out:
        for i, record in enumerate(sampled):
            logger.info(f"[{i+1}/{len(sampled)}] {record['company']} — {record['form_type']}")

            sic = get_sic_for_cik(record["cik"])
            industry_label = None
            if sic:
                # Try exact match, then 4-digit prefix, then first 2 digits
                industry_label = (
                    sic_map.get(sic)
                    or sic_map.get(sic[:4])
                    or sic_map.get(sic[:2] + "00")
                    or "other"
                )

            text = get_filing_text(record["filename"])
            if not text:
                logger.warning(f"No text extracted, skipping: {record['filename']}")
                continue

            row = {
                "text": text,
                "doc_type_label": FORM_TO_DOC_TYPE[record["form_type"]],
                "industry_label": industry_label,
                "source_form": record["form_type"],
                "source_cik": record["cik"],
                "source_sic": sic,
            }
            out.write(json.dumps(row) + "\n")
            written += 1

    logger.info(f"Done. Wrote {written} labeled records to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
