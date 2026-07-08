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

# Target samples per industry (stratified sampling)
SAMPLES_PER_INDUSTRY = 100
# Minimum per form type within each industry bucket (so we get variety)
FORMS_PER_INDUSTRY_BUCKET = 20

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
}

REQUEST_DELAY = 0.15  # seconds between requests (~6-7/sec, well within limits)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        # Handle gzip transparently (urllib does this automatically for gzip)
        return raw.decode("utf-8", errors="replace")


def strip_html(html: str) -> str:
    """Strip HTML/iXBRL tags and decode common entities, keeping readable text."""
    # Remove script/style/head blocks entirely
    text = re.sub(r"<(script|style|head)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    # Remove iXBRL non-numeric wrapper tags but keep their text content
    # e.g. <ix:nonNumeric ...>text</ix:nonNumeric> → text
    text = re.sub(r"<ix:[^>]+>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</ix:[^>]+>", " ", text, flags=re.IGNORECASE)
    # Remove all remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#160;", " ").replace("&#xa0;", " ")
    text = text.replace("&ldquo;", '"').replace("&rdquo;", '"').replace("&lsquo;", "'").replace("&rsquo;", "'")
    # Collapse whitespace
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Drop lines that are pure numbers/whitespace (XBRL inline data noise)
    lines = [l for l in text.splitlines() if not re.match(r"^\s*[\d\s\.\,\-\(\)]+\s*$", l) or len(l.strip()) > 20]
    text = "\n".join(lines)
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
            # filename column may bleed — find the actual edgar/data/ path
            tail = line[98:]
            edgar_idx = tail.find("edgar/data/")
            if edgar_idx == -1:
                continue
            filename = tail[edgar_idx:].strip()
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
    """
    Download the primary document for a filing.

    filename from company.idx is like: edgar/data/CIK/ACCESSION.txt
    That .txt is the full SGML submission container (multi-document bundle).
    The actual filing index page lives at:
      https://www.sec.gov/Archives/edgar/data/CIK/ACCESSION-nodashes/ACCESSION-index.htm
    """
    # Derive accession number and CIK from filename path
    # e.g. edgar/data/1563568/0001437749-23-028283.txt
    parts = filename.split("/")
    if len(parts) < 4:
        return None
    cik = parts[2]
    accession_with_ext = parts[3]
    accession = accession_with_ext.replace(".txt", "")  # e.g. 0001437749-23-028283
    accession_nodash = accession.replace("-", "")       # e.g. 000143774923028283

    index_url = f"{EDGAR_BASE}/Archives/edgar/data/{cik}/{accession_nodash}/{accession}-index.htm"
    try:
        index_html = fetch(index_url)
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        logger.debug(f"Failed to fetch filing index {index_url}: {e}")
        return None

    # Find the primary document (Seq=1 row). Primary docs often use the
    # inline XBRL viewer path /ix?doc=/Archives/... so we match both forms.
    doc_path = None

    # Try Seq=1 row — matches both /Archives/... and /ix?doc=/Archives/...
    seq1 = re.search(
        r'<td[^>]*>\s*1\s*</td>.*?href="(?:/ix\?doc=)?(/Archives/edgar/data/[^"]+\.(?:htm|html|txt))"',
        index_html,
        re.IGNORECASE | re.DOTALL,
    )
    if seq1:
        doc_path = seq1.group(1)

    if not doc_path:
        # Fallback: find doc whose Type column matches the form type
        type_match = re.search(
            r'href="(?:/ix\?doc=)?(/Archives/edgar/data/[^"]+\.(?:htm|html|txt))"[^>]*>[^<]+</a>.*?<td[^>]*>(?:10-K|10-Q|8-K|S-1|20-F|DEF 14A|6-K)\s*</td>',
            index_html,
            re.IGNORECASE | re.DOTALL,
        )
        if type_match:
            doc_path = type_match.group(1)

    if not doc_path:
        # Last resort: first /Archives .htm link that isn't the submission bundle
        last = re.search(
            r'href="(?:/ix\?doc=)?(/Archives/edgar/data/[^"]+\.htm)"',
            index_html,
        )
        if last:
            doc_path = last.group(1)

    if not doc_path:
        logger.debug(f"No primary document found in {index_url}")
        return None

    doc_url = f"{EDGAR_BASE}{doc_path}"
    try:
        raw = fetch(doc_url)
        time.sleep(REQUEST_DELAY)
        text = strip_html(raw)
        if not text:
            return None
        # iXBRL docs have machine-readable metadata before human-readable prose.
        # Skip to the first recognizable section header.
        start = 0
        for marker in ["PART I", "ITEM 1", "Item 1", "MANAGEMENT", "Management's Discussion",
                        "BUSINESS", "Business Overview", "FINANCIAL STATEMENTS"]:
            idx = text.find(marker)
            if 0 < idx < len(text) - 500:
                start = idx
                break
        return text[start:start + 8000]
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
            if len(all_records) >= SAMPLES_PER_INDUSTRY * 50:
                break
        else:
            continue
        break

    logger.info(f"Total candidate filings: {len(all_records)}")

    from collections import defaultdict
    import random

    random.seed(42)

    # Deduplicate by CIK so we don't over-represent prolific filers
    seen_ciks: set[str] = set()
    unique_records = []
    for r in all_records:
        if r["cik"] not in seen_ciks:
            seen_ciks.add(r["cik"])
            unique_records.append(r)
    random.shuffle(unique_records)
    logger.info(f"Unique filers: {len(unique_records)}")

    # Pre-resolve SIC for a large candidate pool so we can stratify by industry.
    # We fetch SIC until we have enough candidates per industry or exhaust the pool.
    target_per_industry = SAMPLES_PER_INDUSTRY
    # How many candidates to resolve before giving up on thin industries
    max_sic_lookups = min(len(unique_records), target_per_industry * 30)

    by_industry: dict[str, list[dict]] = defaultdict(list)
    logger.info(f"Pre-resolving SIC codes for up to {max_sic_lookups} candidates...")

    for i, record in enumerate(unique_records[:max_sic_lookups]):
        if i % 100 == 0:
            counts = {k: len(v) for k, v in by_industry.items()}
            filled = sum(1 for v in by_industry.values() if len(v) >= target_per_industry)
            logger.info(f"  SIC lookup {i}/{max_sic_lookups} — {len(by_industry)} industries, {filled} filled")

        sic = get_sic_for_cik(record["cik"])
        if not sic:
            continue
        industry = (
            sic_map.get(sic)
            or sic_map.get(sic[:4])
            or sic_map.get(sic[:2] + "00")
            or "other"
        )
        record["sic"] = sic
        record["industry_label"] = industry
        by_industry[industry].append(record)

    logger.info(f"Industry candidate pool: { {k: len(v) for k, v in sorted(by_industry.items())} }")

    # Stratified sample: up to target_per_industry per industry
    sampled = []
    for industry, records in by_industry.items():
        random.shuffle(records)
        sampled.extend(records[:target_per_industry])

    logger.info(f"Sampled {len(sampled)} filings to download")

    written = 0
    with open(OUTPUT_FILE, "w") as out:
        for i, record in enumerate(sampled):
            logger.info(f"[{i+1}/{len(sampled)}] {record['company']} — {record['form_type']} — {record['industry_label']}")

            text = get_filing_text(record["filename"])
            if not text:
                logger.warning(f"No text extracted, skipping: {record['filename']}")
                continue

            row = {
                "text": text,
                "doc_type_label": FORM_TO_DOC_TYPE[record["form_type"]],
                "industry_label": record["industry_label"],
                "source_form": record["form_type"],
                "source_cik": record["cik"],
                "source_sic": record["sic"],
            }
            out.write(json.dumps(row) + "\n")
            written += 1

    logger.info(f"Done. Wrote {written} labeled records to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
