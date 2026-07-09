import { useEffect, useState } from "react";
import { fetchResults, clearHistory, searchRecords, exportCsvUrl } from "../api";
import type { ClassificationRecord } from "../types";
import { ResultsTable } from "../components/ResultsTable";
import { ResultDetail } from "../components/ResultDetail";
import styles from "./Page.module.css";

export function History() {
  const [records, setRecords] = useState<ClassificationRecord[]>([]);
  const [selected, setSelected] = useState<ClassificationRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQ, setSearchQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [isSearchMode, setIsSearchMode] = useState(false);

  useEffect(() => {
    fetchResults().then(setRecords).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);

  async function handleSearch(q: string) {
    setSearchQ(q);
    if (!q.trim()) {
      setIsSearchMode(false);
      const res = await fetchResults();
      setRecords(res);
      return;
    }
    setSearching(true);
    setIsSearchMode(true);
    try {
      const res = await searchRecords(q);
      setRecords(res);
      setSelected(null);
    } finally {
      setSearching(false);
    }
  }

  async function handleClear() {
    if (!confirm(`Delete all classification records? This cannot be undone.`)) return;
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
        <p className={styles.subtitle}>
          {isSearchMode
            ? `${records.length} result${records.length !== 1 ? "s" : ""} for "${searchQ}"`
            : `${records.length} document${records.length !== 1 ? "s" : ""} classified`}
        </p>
      </div>

      <div className={styles.historyToolbar}>
        <input
          className={styles.searchInput}
          placeholder="Search filenames and summaries…"
          value={searchQ}
          onChange={(e) => handleSearch(e.target.value)}
        />
        {searching && <span className={styles.muted}>Searching…</span>}
        <a href={exportCsvUrl()} download className={styles.toolbarBtn}>Export CSV</a>
        {records.length > 0 && !isSearchMode && (
          <button className={styles.clearBtn} onClick={handleClear}>Clear history</button>
        )}
      </div>

      {records.length === 0 ? (
        <p className={styles.muted}>
          {isSearchMode ? "No documents match your search." : "No documents classified yet. Go to Classify to get started."}
        </p>
      ) : (
        <>
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
