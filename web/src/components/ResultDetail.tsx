import { useState } from "react";
import type { ClassificationRecord, PainPoint } from "../types";
import { fetchActionItems } from "../api";
import { ConfidentialityBadge, ImportanceBadge } from "./Badge";
import { CorrectLabels } from "./CorrectLabels";
import styles from "./ResultDetail.module.css";

function PainPointCard({ point }: { point: PainPoint }) {
  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);

  async function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && items === null && !loading) {
      setLoading(true);
      try {
        setItems(await fetchActionItems(point.label, point.context ?? ""));
      } catch {
        setItems([]);
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <div className={styles.painCard}>
      {point.category && <span className={styles.ppCategory}>{point.category}</span>}
      <p className={styles.ppLabel}>{point.label}</p>
      {point.context && <p className={styles.ppContext}>{point.context}</p>}
      {point.question && (
        <p className={styles.ppQuestion}>
          <span className={styles.ppQuestionIcon}>→</span>
          {point.question}
        </p>
      )}

      <button className={styles.ppMoreBtn} onClick={toggle}>
        {expanded ? "Hide action items" : "Suggested action items"}
        <span className={`${styles.ppChevron} ${expanded ? styles.ppChevronOpen : ""}`}>▾</span>
      </button>

      {expanded && (
        <div className={styles.ppActions}>
          {loading && <p className={styles.ppActionsLoading}>Generating action items…</p>}
          {!loading && items && items.length > 0 && (
            <ul className={styles.ppActionsList}>
              {items.map((it, i) => <li key={i} className={styles.ppActionItem}>{it}</li>)}
            </ul>
          )}
          {!loading && items && items.length === 0 && (
            <p className={styles.ppActionsLoading}>No suggestions available.</p>
          )}
        </div>
      )}
    </div>
  );
}

export function ResultDetail({ record }: { record: ClassificationRecord }) {
  const flaggedDimensions = [
    record.document_type?.needs_review && "doc type",
    record.industry?.needs_review && "industry",
    record.confidentiality?.needs_review && "confidentiality",
    record.importance_level?.needs_review && "importance",
  ].filter(Boolean);
  const needsReview = flaggedDimensions.length > 0;

  return (
    <div className={styles.wrapper}>

      {/* Summary */}
      {record.summary && (
        <p className={styles.summary}>{record.summary}</p>
      )}

      {/* Review flag */}
      {needsReview && (
        <div className={styles.reviewBanner}>
          <span className={styles.reviewIcon}>⚠</span>
          <span>
            Flagged for human review — low confidence on {flaggedDimensions.join(" and ")}.
          </span>
        </div>
      )}

      {/* Classification grid */}
      <div className={styles.grid}>
        <StatCard label="Doc Type" value={record.document_type?.label ?? "—"} />

        <StatCard label="Confidentiality" value="">
          {record.confidentiality
            ? <ConfidentialityBadge label={record.confidentiality.label} />
            : <span className={styles.dash}>—</span>}
          {record.confidentiality && (
            <ConfBar value={record.confidentiality.confidence} color={confColor(record.confidentiality.label)} />
          )}
          {record.confidentiality && (
            <span className={styles.subtext}>{(record.confidentiality.confidence * 100).toFixed(0)}% confidence</span>
          )}
        </StatCard>

        <StatCard label="Importance" value="">
          {record.importance_level
            ? <ImportanceBadge label={record.importance_level.label} />
            : <span className={styles.dash}>—</span>}
          {record.importance_level && (
            <ConfBar value={record.importance_level.confidence} color={impColor(record.importance_level.label)} />
          )}
          {record.importance_level && (
            <span className={styles.subtext}>{(record.importance_level.confidence * 100).toFixed(0)}% confidence</span>
          )}
        </StatCard>

        <StatCard label="Industry" value={record.industry?.label ?? "—"}>
          {record.industry && (
            <span className={styles.subtext}>
              {record.industry.user_provided ? "user-provided" : "auto-detected"}
            </span>
          )}
        </StatCard>
      </div>

      {/* Human label correction (feedback loop → retraining data) */}
      <CorrectLabels
        recordId={record.id}
        docType={record.document_type?.label ?? null}
        industry={record.industry?.label ?? null}
        userDocType={record.user_doc_type}
        userIndustry={record.user_industry}
      />

      {/* Pain points */}
      {record.pain_points.length > 0 && (
        <div className={styles.section}>
          <p className={styles.painHeader}>
            Pain Points
            <span className={styles.painCount}>{record.pain_points.length}</span>
          </p>
          <div className={styles.painList}>
            {record.pain_points.map((pp, i) => (
              <PainPointCard key={pp.label + i} point={pp} />
            ))}
          </div>
        </div>
      )}

      {record.pain_points.length === 0 && (
        <p className={styles.noPainPoints}>No structural pain points identified in this document.</p>
      )}

      {/* Rationale */}
      {record.confidentiality?.rationale && (
        <Rationale
          label="Why this confidentiality level?"
          text={record.confidentiality.rationale}
          flagged={record.confidentiality.needs_review}
        />
      )}
      {record.importance_level?.rationale && (
        <Rationale
          label="Why this importance level?"
          text={record.importance_level.rationale}
          flagged={record.importance_level.needs_review}
        />
      )}

      {record.error && <div className={styles.error}>Pipeline error: {record.error}</div>}
    </div>
  );
}

function StatCard({ label, value, children }: { label: string; value: string; children?: React.ReactNode }) {
  return (
    <div className={styles.statCard}>
      <p className={styles.statLabel}>{label}</p>
      {value && <p className={styles.statValue}>{value}</p>}
      {children}
    </div>
  );
}

function ConfBar({ value, color }: { value: number; color: string }) {
  return (
    <div className={styles.confBarTrack}>
      <div className={styles.confBarFill} style={{ width: `${value * 100}%`, background: color }} />
    </div>
  );
}

function Rationale({ label, text, flagged }: { label: string; text: string; flagged: boolean }) {
  return (
    <details className={styles.rationale}>
      <summary className={styles.rationaleSummary}>
        <span className={styles.summaryChevron}>▶</span>
        {label}
        {flagged && <span className={styles.flag}>low confidence</span>}
      </summary>
      <p className={styles.rationaleText}>{text}</p>
    </details>
  );
}

function confColor(label: string) {
  return label === "public" ? "var(--conf-public)" : label === "sensitive" ? "var(--conf-sensitive)" : "var(--conf-restricted)";
}

function impColor(label: string) {
  return label === "high" ? "var(--imp-high)" : label === "medium" ? "var(--imp-medium)" : "var(--imp-low)";
}
