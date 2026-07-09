import { useState } from "react";
import type { ClassificationRecord, Portfolio } from "../types";
import { ResultsTable } from "./ResultsTable";
import { ResultDetail } from "./ResultDetail";
import { exportCsvUrl } from "../api";
import styles from "./PortfolioView.module.css";

interface Props {
  portfolio: Portfolio;
  records: ClassificationRecord[];
  onDelete?: () => void;
}

export function PortfolioView({ portfolio, records, onDelete }: Props) {
  const [selected, setSelected] = useState<ClassificationRecord | null>(null);

  const confCounts = tally(records, (r) => r.confidentiality?.label);
  const impCounts = tally(records, (r) => r.importance_level?.label);
  const topPain = topN(records.flatMap((r) => r.pain_points), (p) => p.label, 6);
  const needsReview = records.filter((r) =>
    r.confidentiality?.needs_review || r.importance_level?.needs_review ||
    r.document_type?.needs_review || r.industry?.needs_review
  ).length;

  return (
    <div className={styles.wrapper}>
      {/* Header */}
      <div className={styles.header}>
        <div>
          <h2 className={styles.name}>{portfolio.name}</h2>
          <p className={styles.meta}>
            {records.length} document{records.length !== 1 ? "s" : ""}
            {" · "}
            {new Date(portfolio.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
          </p>
        </div>
        <div className={styles.actions}>
          <a href={exportCsvUrl(portfolio.id)} download className={styles.exportBtn}>Export CSV</a>
          {onDelete && (
            <button className={styles.deleteBtn} onClick={onDelete}>Delete</button>
          )}
        </div>
      </div>

      {/* Theme */}
      {portfolio.theme && (
        <div className={styles.theme}>
          <p className={styles.themeLabel}>Key Themes</p>
          <p className={styles.themeText}>{portfolio.theme}</p>
        </div>
      )}

      {/* Stats grid */}
      <div className={styles.statsGrid}>
        <StatGroup label="Confidentiality" items={confCounts} colorFn={confColor} />
        <StatGroup label="Importance" items={impCounts} colorFn={impColor} />
        {topPain.length > 0 && (
          <div className={styles.statGroup}>
            <p className={styles.statGroupLabel}>Top Pain Points</p>
            {topPain.map(([label, count]) => (
              <div key={label} className={styles.statRow}>
                <span className={styles.statCount}>{count}×</span>
                <span className={styles.statKey}>{label}</span>
              </div>
            ))}
          </div>
        )}
        <div className={styles.statGroup}>
          <p className={styles.statGroupLabel}>Flags</p>
          <div className={styles.statRow}>
            <span className={styles.statCount}>{needsReview}</span>
            <span className={styles.statKey} style={{ color: needsReview > 0 ? "var(--conf-sensitive)" : "var(--text-muted)" }}>
              need{needsReview !== 1 ? "" : "s"} review
            </span>
          </div>
        </div>
      </div>

      {/* Table */}
      <ResultsTable records={records} onSelect={setSelected} selectedId={selected?.id} />

      {selected && (
        <div className={styles.detailCard}>
          <p className={styles.detailFilename}>{selected.filename}</p>
          <ResultDetail record={selected} />
        </div>
      )}
    </div>
  );
}

function StatGroup({ label, items, colorFn }: { label: string; items: Record<string, number>; colorFn: (k: string) => string }) {
  return (
    <div className={styles.statGroup}>
      <p className={styles.statGroupLabel}>{label}</p>
      {Object.entries(items).map(([k, v]) => (
        <div key={k} className={styles.statRow}>
          <span className={styles.statCount}>{v}</span>
          <span className={styles.statKey} style={{ color: colorFn(k) }}>{k}</span>
        </div>
      ))}
    </div>
  );
}

function tally<T>(items: T[], key: (i: T) => string | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  for (const item of items) { const k = key(item); if (k) out[k] = (out[k] ?? 0) + 1; }
  return out;
}

function topN<T>(items: T[], key: (i: T) => string, n: number): [string, number][] {
  return Object.entries(tally(items, key)).sort((a, b) => b[1] - a[1]).slice(0, n);
}

function confColor(l: string) {
  return l === "public" ? "var(--conf-public)" : l === "sensitive" ? "var(--conf-sensitive)" : "var(--conf-restricted)";
}
function impColor(l: string) {
  return l === "high" ? "var(--imp-high)" : l === "medium" ? "var(--imp-medium)" : "var(--imp-low)";
}
