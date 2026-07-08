import type { ClassificationRecord } from "./types";

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
