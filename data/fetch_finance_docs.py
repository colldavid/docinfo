"""
Fetch 40 finance-sector EDGAR filings as individual .txt files.
Output: documents/finance_edgar/

Usage:
    PYTHONPATH=. .venv/Scripts/python data/fetch_finance_docs.py
"""
import json
import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

EDGAR_BASE = "https://www.sec.gov"
OUT_DIR = Path(__file__).parent.parent / "documents" / "finance_edgar"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "DocInfo research davidalejcoll@gmail.com",
    "Accept-Encoding": "identity",
}

# Finance SIC codes (banks, insurance, investment, REITs, etc.)
FINANCE_SICS = [
    "6020", "6021", "6022",  # commercial banks
    "6141", "6153", "6159",  # personal credit, short-term business credit
    "6199", "6200", "6211",  # finance services, security dealers
    "6311", "6321", "6331",  # life/health/fire insurance
    "6411",                   # insurance agents
    "6512", "6552",           # real estate operators / land subdividers
    "6726",                   # investment offices
]

TARGET_FORMS = ["10-K", "10-Q", "8-K"]
TARGET_COUNT = 40


class MLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fed = []
    def handle_data(self, d):
        self.fed.append(d)
    def get_data(self):
        return " ".join(self.fed)


def strip_html(html: str) -> str:
    s = MLStripper()
    s.feed(html)
    text = s.get_data()
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fetch(url: str, retries=3) -> bytes | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except Exception as e:
            print(f"  fetch error ({url}): {e}, attempt {attempt+1}/{retries}")
            time.sleep(2 ** attempt)
    return None


def get_full_index_entries(year=2024, quarter=1):
    """Download the EDGAR full-index and return list of (form, company, cik, filename)."""
    url = f"{EDGAR_BASE}/Archives/edgar/full-index/{year}/QTR{quarter}/company.idx"
    print(f"Fetching index: {url}")
    data = fetch(url)
    if not data:
        return []
    lines = data.decode("latin-1").splitlines()
    entries = []
    for line in lines[10:]:  # skip header rows
        if len(line) < 98:
            continue
        form = line[62:74].strip()
        company = line[0:62].strip()
        cik = line[74:86].strip()
        filename = line[98:].strip()
        if form in TARGET_FORMS:
            entries.append((form, company, cik, filename))
    return entries


def get_sic(cik: str) -> str | None:
    url = f"{EDGAR_BASE}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=1&search_text=&output=atom"
    data = fetch(url)
    if not data:
        return None
    text = data.decode("utf-8", errors="ignore")
    m = re.search(r'<assigned-sic>(\d+)</assigned-sic>', text)
    return m.group(1) if m else None


def get_filing_text(filename: str) -> str | None:
    idx_url = f"{EDGAR_BASE}/Archives/{filename}"
    data = fetch(idx_url)
    if not data:
        return None
    idx_text = data.decode("utf-8", errors="ignore")
    # Find the primary document link
    doc_match = re.search(r'<td><a href="(/Archives/edgar/data/[^"]+\.(htm|txt))"', idx_text, re.IGNORECASE)
    if not doc_match:
        # Try plain index format
        lines = idx_text.splitlines()
        for line in lines:
            if re.search(r'\.(htm|txt)', line, re.IGNORECASE) and 'index' not in line.lower():
                parts = line.split()
                if parts:
                    doc_path = parts[-1]
                    if doc_path.startswith('/'):
                        doc_url = f"{EDGAR_BASE}{doc_path}"
                    else:
                        base = idx_url.rsplit('/', 1)[0]
                        doc_url = f"{base}/{doc_path}"
                    break
        else:
            return None
    else:
        doc_url = f"{EDGAR_BASE}{doc_match.group(1)}"

    time.sleep(0.15)
    doc_data = fetch(doc_url)
    if not doc_data:
        return None
    raw = doc_data.decode("utf-8", errors="ignore")
    if "<html" in raw.lower() or "<HTML" in raw:
        text = strip_html(raw)
    else:
        text = re.sub(r'\s+', ' ', raw).strip()
    return text[:15000] if text else None


def main():
    existing = list(OUT_DIR.glob("*.txt"))
    print(f"Already have {len(existing)} docs in {OUT_DIR}")
    if len(existing) >= TARGET_COUNT:
        print("Already at target count, nothing to do.")
        return

    entries = get_full_index_entries(2024, 1)
    print(f"Index has {len(entries)} matching form entries")

    saved = len(existing)
    checked_ciks: dict[str, str | None] = {}

    for form, company, cik, filename in entries:
        if saved >= TARGET_COUNT:
            break

        # Check SIC (cache per CIK)
        if cik not in checked_ciks:
            time.sleep(0.1)
            checked_ciks[cik] = get_sic(cik)
        sic = checked_ciks[cik]

        if sic not in FINANCE_SICS:
            continue

        print(f"[{saved+1}/{TARGET_COUNT}] {form} — {company} (SIC {sic})")

        # Build index URL
        idx_url = filename if filename.startswith("edgar/") else filename
        time.sleep(0.15)
        text = get_filing_text(idx_url)
        if not text or len(text) < 500:
            print("  skipped (too short or no text)")
            continue

        safe_name = re.sub(r'[^\w\-]', '_', company)[:40]
        out_path = OUT_DIR / f"{safe_name}_{form.replace('-','')}.txt"
        # avoid clobbering
        if out_path.exists():
            out_path = OUT_DIR / f"{safe_name}_{form.replace('-','')}_{cik}.txt"

        out_path.write_text(text[:15000], encoding="utf-8")
        print(f"  saved → {out_path.name}")
        saved += 1
        time.sleep(0.2)

    print(f"\nDone. {saved} docs in documents/finance_edgar/")


if __name__ == "__main__":
    main()
