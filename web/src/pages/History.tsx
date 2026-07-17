import { useEffect, useRef, useState } from "react";
import { fetchResults, clearHistory, searchRecords, exportCsvUrl } from "../api";
import type { ClassificationRecord } from "../types";
import { ConfidentialityBadge, ImportanceBadge } from "../components/Badge";
import { ResultDetail } from "../components/ResultDetail";
import styles from "./History.module.css";
import pageStyles from "./Page.module.css";

export function History() {
  const [records, setRecords] = useState<ClassificationRecord[]>([]);
  const [selected, setSelected] = useState<ClassificationRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQ, setSearchQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [isSearchMode, setIsSearchMode] = useState(false);
  const detailRef = useRef<HTMLDivElement>(null);

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

  function handleSelect(r: ClassificationRecord) {
    const closing = selected?.id === r.id;
    setSelected(closing ? null : r);
    if (!closing) {
      setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
    }
  }

  if (loading) return <p className={pageStyles.muted}>Loading…</p>;
  if (error) return <div className={pageStyles.error}>Error: {error}</div>;

  return (
    <div className={pageStyles.page}>
      <div className={pageStyles.header}>
        <div className={pageStyles.accentLine} />
        <h1 className={pageStyles.title}>History</h1>
        <p className={pageStyles.subtitle}>
          {isSearchMode
            ? `${records.length} result${records.length !== 1 ? "s" : ""} for "${searchQ}"`
            : `${records.length} document${records.length !== 1 ? "s" : ""} classified`}
        </p>
      </div>

      <div className={pageStyles.historyToolbar}>
        <input
          className={pageStyles.searchInput}
          placeholder="Search filenames and summaries…"
          value={searchQ}
          onChange={(e) => handleSearch(e.target.value)}
        />
        {searching && <span className={pageStyles.muted}>Searching…</span>}
        <a href={exportCsvUrl()} download className={pageStyles.toolbarBtn}>Export CSV</a>
        {records.length > 0 && !isSearchMode && (
          <button className={pageStyles.clearBtn} onClick={handleClear}>Clear history</button>
        )}
      </div>

      {records.length === 0 ? (
        <p className={pageStyles.muted}>
          {isSearchMode ? "No documents match your search." : "No documents classified yet. Go to Classify to get started."}
        </p>
      ) : (
        <div className={styles.list}>
          {records.map((r) => {
            const isOpen = selected?.id === r.id;
            const needsReview = r.confidentiality?.needs_review || r.importance_level?.needs_review ||
              r.document_type?.needs_review || r.industry?.needs_review;
            return (
              <div key={r.id} className={`${styles.row} ${isOpen ? styles.rowOpen : ""}`}>
                <button className={styles.rowBtn} onClick={() => handleSelect(r)}>
                  <span className={styles.filename}>{r.filename}</span>
                  <span className={styles.meta}>
                    <span className={styles.metaTag}>{r.document_type?.label ?? "—"}</span>
                    <span className={styles.metaTag}>{r.industry?.label ?? "—"}</span>
                    {r.confidentiality && <ConfidentialityBadge label={r.confidentiality.label} />}
                    {r.importance_level && <ImportanceBadge label={r.importance_level.label} />}
                    {needsReview && <span className={styles.flag}>⚠ review</span>}
                    <span className={styles.date}>
                      {new Date(r.classified_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                    </span>
                  </span>
                </button>
                {isOpen && (
                  <div className={styles.detail} ref={detailRef}>
                    <ResultDetail record={r} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
