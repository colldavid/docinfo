import type { ClassificationRecord } from "../types";
import styles from "./BatchSummary.module.css";

interface Props { records: ClassificationRecord[]; }

export function BatchSummary({ records }: Props) {
  const total = records.length;
  const errors = records.filter((r) => r.error).length;
  const confCounts = tally(records, (r) => r.confidentiality?.label);
  const impCounts = tally(records, (r) => r.importance_level?.label);

  const allPain = records.flatMap((r) => r.pain_points);
  const painByCategory = topN(allPain, (pp) => pp.category || "Operations & Process", 7);
  const totalPain = allPain.length;

  const lowConfidence = records.filter((r) =>
    r.document_type?.needs_review || r.industry?.needs_review ||
    r.confidentiality?.needs_review || r.importance_level?.needs_review
  );

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <span className={styles.title}>
          <span className={styles.accentLine} />
          {total} document{total !== 1 ? "s" : ""} classified
        </span>
        {errors > 0 && <span className={styles.errorBadge}>{errors} error{errors !== 1 ? "s" : ""}</span>}
      </div>

      <div className={styles.grid}>
        <Group label="Confidentiality" items={confCounts} colorClass={(k) => styles[`conf_${k}`]} />
        <Group label="Importance" items={impCounts} colorClass={(k) => styles[`imp_${k}`]} />
      </div>

      {painByCategory.length > 0 && (
        <div className={styles.painThemes}>
          <p className={styles.painThemesLabel}>
            Pain Points by Theme
            <span className={styles.painThemesTotal}>{totalPain} identified across {total} doc{total !== 1 ? "s" : ""}</span>
          </p>
          <div className={styles.painThemesGrid}>
            {painByCategory.map(([category, count]) => {
              const pct = Math.round((count / totalPain) * 100);
              return (
                <div key={category} className={styles.painThemeRow}>
                  <div className={styles.painThemeTop}>
                    <span className={styles.painThemeName}>{category}</span>
                    <span className={styles.painThemePct}>{pct}%</span>
                  </div>
                  <div className={styles.painThemeTrack}>
                    <div className={styles.painThemeFill} style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {lowConfidence.length > 0 && (
        <div className={styles.alert}>
          <p className={styles.alertTitle}>Needs review — {lowConfidence.length} doc{lowConfidence.length !== 1 ? "s" : ""} with low confidence</p>
          {lowConfidence.map((r) => {
            const reasons = [
              r.document_type?.needs_review && "doc type",
              r.industry?.needs_review && "industry",
              r.confidentiality?.needs_review && "confidentiality",
              r.importance_level?.needs_review && "importance",
            ].filter(Boolean);
            return (
              <p key={r.id} className={styles.alertRow}>
                <span className={styles.alertFile}>{r.filename}</span>
                <span className={styles.alertReason}>{reasons.join(", ")}</span>
              </p>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Group({ label, items, colorClass }: { label: string; items: Record<string, number>; colorClass: (k: string) => string }) {
  return (
    <div className={styles.group}>
      <p className={styles.groupLabel}>{label}</p>
      {Object.entries(items).map(([k, v]) => (
        <p key={k} className={styles.groupRow}>
          <span className={styles.groupCount}>{v}</span>
          <span className={`${styles.groupKey} ${colorClass(k)}`}>{k}</span>
        </p>
      ))}
    </div>
  );
}

function tally<T>(items: T[], key: (item: T) => string | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  for (const item of items) { const k = key(item); if (k) out[k] = (out[k] ?? 0) + 1; }
  return out;
}

function topN<T>(items: T[], key: (item: T) => string, n: number): [string, number][] {
  return Object.entries(tally(items, key)).sort((a, b) => b[1] - a[1]).slice(0, n);
}
