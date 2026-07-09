import type { ClassificationRecord, Portfolio } from "./types";

// In dev, Vite proxies /api → localhost:8000. In prod (served by FastAPI), no prefix needed.
const BASE = import.meta.env.DEV ? "/api" : "";

export async function classifyBatch(
  files: File[],
  industry: string | null
): Promise<ClassificationRecord[]> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  if (industry) form.append("industry", industry);
  const res = await fetch(`${BASE}/classify/batch`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchResults(limit = 100, offset = 0): Promise<ClassificationRecord[]> {
  const res = await fetch(`${BASE}/results?limit=${limit}&offset=${offset}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchNeedsReview(): Promise<ClassificationRecord[]> {
  const res = await fetch(`${BASE}/results/needs-review`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function clearHistory(): Promise<void> {
  const res = await fetch(`${BASE}/results`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
}

export async function searchRecords(q: string): Promise<ClassificationRecord[]> {
  const res = await fetch(`${BASE}/search?q=${encodeURIComponent(q)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function exportCsvUrl(portfolioId?: number): string {
  const base = import.meta.env.DEV ? "/api" : "";
  return portfolioId ? `${base}/results/export?portfolio_id=${portfolioId}` : `${base}/results/export`;
}

export async function createPortfolio(name: string, recordIds: number[]): Promise<Portfolio> {
  const res = await fetch(`${BASE}/portfolios`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, record_ids: recordIds }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchPortfolios(): Promise<Portfolio[]> {
  const res = await fetch(`${BASE}/portfolios`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchPortfolio(id: number): Promise<Portfolio> {
  const res = await fetch(`${BASE}/portfolios/${id}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deletePortfolio(id: number): Promise<void> {
  const res = await fetch(`${BASE}/portfolios/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
}
