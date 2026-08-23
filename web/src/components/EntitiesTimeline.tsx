import { useState } from "react";
import styles from "./EntitiesTimeline.module.css";

const BASE = import.meta.env.DEV ? "/api" : "";

interface Entity {
  name: string;
  type: string;
  mentions: string[];
}

interface TimelineEvent {
  date: string;
  description: string;
  source: string;
  inferred?: boolean;
}

interface EntitiesResponse {
  entities: Entity[];
  events: TimelineEvent[];
  undated_events?: TimelineEvent[];
  checked_docs: number;
  skipped_docs: number;
  cached: boolean;
}

export function EntitiesTimeline({ portfolioId }: { portfolioId: number }) {
  const [result, setResult] = useState<EntitiesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(refresh: boolean) {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${BASE}/portfolios/${portfolioId}/entities${refresh ? "?refresh=true" : ""}`,
        { method: "POST" }
      );
      if (!res.ok) throw new Error(`Extraction failed (${res.status})`);
      setResult(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Extraction failed");
    } finally {
      setLoading(false);
    }
  }

  // Only known after the first run — before that we can't name a document count.
  const docCount = result?.checked_docs;
  const isEmpty =
    result !== null && result.entities.length === 0 && result.events.length === 0;

  return (
    <div className={styles.section}>
      <p className={styles.sectionLabel}>Entities &amp; Timeline</p>

      {!result && !loading && (
        <button className={styles.runBtn} onClick={() => run(false)} disabled={loading}>
          Map entities &amp; timeline
        </button>
      )}

      {loading && (
        <p className={styles.loading}>
          {docCount ? `Reading ${docCount} documents…` : "Reading documents…"}
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
          {isEmpty && (
            <p className={styles.empty}>No entities/dated events extracted.</p>
          )}

          {result.entities.length > 0 && (
            <div className={styles.block}>
              <p className={styles.blockLabel}>Recurring entities</p>
              <div className={styles.chips}>
                {result.entities.map((e, i) => (
                  <span
                    key={i}
                    className={styles.chip}
                    title={`Mentioned in: ${e.mentions.join(", ")}`}
                  >
                    <span className={styles.chipName}>{e.name}</span>
                    <span className={styles.chipType}>{e.type}</span>
                    {e.mentions.length > 1 && (
                      <span className={styles.chipCount}>×{e.mentions.length}</span>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}

          {result.events.length > 0 && (
            <div className={styles.block}>
              <p className={styles.blockLabel}>Chronology</p>
              <div className={styles.timeline}>
                {result.events.map((ev, i) => (
                  <div key={i} className={styles.event}>
                    <span className={styles.eventDate}>
                      {ev.date}
                      {ev.inferred && <span className={styles.inferred} title="Date resolved from document context, not stated verbatim"> (inferred)</span>}
                    </span>
                    <div className={styles.eventBody}>
                      <span className={styles.eventText}>{ev.description}</span>
                      <span className={styles.eventSource}>{ev.source}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {(result.undated_events?.length ?? 0) > 0 && (
            <div className={styles.block}>
              <p className={styles.blockLabel}>Undated</p>
              <div className={styles.timeline}>
                {result.undated_events!.map((ev, i) => (
                  <div key={i} className={`${styles.event} ${styles.eventUndated}`}>
                    <span className={styles.eventDate}>{ev.date}</span>
                    <div className={styles.eventBody}>
                      <span className={styles.eventText}>{ev.description}</span>
                      <span className={styles.eventSource}>{ev.source}</span>
                    </div>
                  </div>
                ))}
              </div>
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
