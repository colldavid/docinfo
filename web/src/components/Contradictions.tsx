import { useState } from "react";
import styles from "./Contradictions.module.css";

const BASE = import.meta.env.DEV ? "/api" : "";

interface Contradiction {
  doc_a: string;
  doc_b: string;
  claim_a: string;
  claim_b: string;
  explanation: string;
}

interface ContradictionsResponse {
  contradictions: Contradiction[];
  checked_docs: number;
  skipped_docs: number;
  cached: boolean;
}

export function Contradictions({ portfolioId }: { portfolioId: number }) {
  const [result, setResult] = useState<ContradictionsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(refresh: boolean) {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${BASE}/portfolios/${portfolioId}/contradictions${refresh ? "?refresh=true" : ""}`,
        { method: "POST" }
      );
      if (!res.ok) throw new Error(`Analysis failed (${res.status})`);
      setResult(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setLoading(false);
    }
  }

  // Only known after the first run — before that we can't name a document count.
  const docCount = result?.checked_docs;

  return (
    <div className={styles.section}>
      <p className={styles.sectionLabel}>Consistency Check</p>

      {!result && !loading && (
        <button className={styles.runBtn} onClick={() => run(false)} disabled={loading}>
          Check for contradictions
        </button>
      )}

      {loading && (
        <p className={styles.loading}>
          {docCount ? `Analyzing ${docCount} documents…` : "Analyzing…"}
        </p>
      )}

      {error && !loading && (
        <div className={styles.error}>
          <span>{error}</span>
          <button className={styles.retryBtn} onClick={() => run(false)}>Try again</button>
        </div>
      )}

      {result && !loading && (
        <>
          {result.contradictions.length === 0 ? (
            <p className={styles.clean}>✓ No cross-document contradictions detected.</p>
          ) : (
            <div className={styles.list}>
              {result.contradictions.map((c, i) => (
                <div key={i} className={styles.card}>
                  <div className={styles.claim}>
                    <span className={styles.claimDoc}>{c.doc_a}</span>
                    <span className={styles.claimText}>{c.claim_a}</span>
                  </div>
                  <div className={styles.claim}>
                    <span className={styles.claimDoc}>{c.doc_b}</span>
                    <span className={styles.claimText}>{c.claim_b}</span>
                  </div>
                  {c.explanation && <p className={styles.explanation}>{c.explanation}</p>}
                </div>
              ))}
            </div>
          )}

          <div className={styles.footer}>
            {result.skipped_docs > 0 && (
              <span className={styles.skipped}>
                {result.skipped_docs} document{result.skipped_docs !== 1 ? "s" : ""} skipped (no extracted text)
              </span>
            )}
            <button className={styles.rerunBtn} onClick={() => run(true)}>Re-run</button>
          </div>
        </>
      )}
    </div>
  );
}
