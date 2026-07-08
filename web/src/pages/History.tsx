import { useEffect, useState } from "react";
import { fetchResults, clearHistory } from "../api";
import type { ClassificationRecord } from "../types";
import { ResultsTable } from "../components/ResultsTable";
import { ResultDetail } from "../components/ResultDetail";
import styles from "./Page.module.css";

export function History() {
  const [records, setRecords] = useState<ClassificationRecord[]>([]);
  const [selected, setSelected] = useState<ClassificationRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchResults().then(setRecords).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);

  async function handleClear() {
    if (!confirm(`Delete all ${records.length} classification records? This cannot be undone.`)) return;
    await clearHistory();
    setRecords([]);
    setSelected(null);
  }

  if (loading) return <p className={styles.muted}>Loading…</p>;
  if (error) return <div className={styles.error}>Error: {error}</div>;

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div className={styles.accentLine} />
        <h1 className={styles.title}>History</h1>
        <p className={styles.subtitle}>{records.length} document{records.length !== 1 ? "s" : ""} classified</p>
      </div>

      {records.length === 0 ? (
        <p className={styles.muted}>No documents classified yet. Go to <strong>Classify</strong> to get started.</p>
      ) : (
        <>
          <button className={styles.clearBtn} onClick={handleClear}>Clear history</button>
          <ResultsTable records={records} onSelect={setSelected} selectedId={selected?.id} />
          {selected && (
            <div className={styles.detailCard}>
              <p className={styles.detailFilename}>{selected.filename}</p>
              <ResultDetail record={selected} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
