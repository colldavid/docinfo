import json
from collections import Counter
from pathlib import Path
lines = Path('data/edgar/labeled_samples.jsonl').read_text(encoding='utf-8').splitlines()
records = [json.loads(l) for l in lines if l.strip()]
print(f'Total records: {len(records)}')
print('\nDoc type counts:')
for k,v in sorted(Counter(r["doc_type_label"] for r in records).items()): print(f'  {v:>5}  {k}')
print('\nIndustry counts:')
for k,v in sorted(Counter(r["industry_label"] for r in records).items()): print(f'  {v:>5}  {k}')
