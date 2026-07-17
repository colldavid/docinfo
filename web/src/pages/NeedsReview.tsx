import { useEffect, useRef, useState } from "react";
import { fetchNeedsReview } from "../api";
import type { ClassificationRecord } from "../types";
import { ResultDetail } from "../components/ResultDetail";
import styles from "./History.module.css";
import pageStyles from "./Page.module.css";

function reviewReasons(r: ClassificationRecord): string[] {
  const reasons: string[] = [];
  if (r.document_type?.needs_review) reasons.push("doc type");
  if (r.industry?.needs_review) reasons.push("industry");
  if (r.confidentiality?.needs_review) reasons.push("confidentiality");
  if (r.importance_level?.needs_review) reasons.push("importance");
  return reasons;
}

export function NeedsReview() {
  const [records, setRecords] = useState<ClassificationRecord[]>([]);
  const [selected, setSelected] = useState<ClassificationRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchNeedsReview().then(setRecords).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);

  function handleSelect(r: ClassificationRecord) {
    const closing = selected?.id === r.id;
    setSelected(closing ? null : r);
    if (!closing) setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
  }

  if (loading) return <p className={pageStyles.muted}>Loading…</p>;
  if (error) return <div className={pageStyles.error}>Error: {error}</div>;

  return (
    <div className={pageStyles.page}>
      <div className={pageStyles.header}>
        <div className={pageStyles.accentLine} />
        <h1 className={pageStyles.title}>Needs Review</h1>
        <p className={pageStyles.subtitle}>Documents where the classifier had low confidence on a specific dimension.</p>
      </div>

      {records.length === 0 ? (
        <div className={pageStyles.allClear}>✓ No documents currently flagged for review.</div>
      ) : (
        <div className={styles.list}>
          {records.map((r) => {
            const isOpen = selected?.id === r.id;
            const reasons = reviewReasons(r);
            return (
              <div key={r.id} className={`${styles.row} ${isOpen ? styles.rowOpen : ""}`}>
                <button className={styles.rowBtn} onClick={() => handleSelect(r)}>
                  <span className={styles.filename}>{r.filename}</span>
                  <span className={styles.meta}>
                    <span className={styles.reviewReason}>
                      low confidence: {reasons.join(", ")}
                    </span>
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
