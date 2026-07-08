import type { ClassificationRecord } from "../types";
import styles from "./BatchSummary.module.css";

interface Props { records: ClassificationRecord[]; }

export function BatchSummary({ records }: Props) {
  const total = records.length;
  const errors = records.filter((r) => r.error).length;
  const confCounts = tally(records, (r) => r.confidentiality?.label);
  const impCounts = tally(records, (r) => r.importance_level?.label);
  const topPainPoints = topN(records.flatMap((r) => r.pain_points), (pp) => pp.label, 5);
  const highPriority = records.filter((r) => r.importance_level?.label === "high" || r.confidentiality?.label === "restricted");

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
        {topPainPoints.length > 0 && (
          <div className={styles.group}>
            <p className={styles.groupLabel}>Top Pain Points</p>
            {topPainPoints.map(([label, count]) => (
              <p key={label} className={styles.groupRow}>
                <span className={styles.groupCount}>{count}×</span>
                <span className={styles.groupKey}>{label}</span>
              </p>
            ))}
          </div>
        )}
      </div>

      {highPriority.length > 0 && (
        <div className={styles.alert}>
          <p className={styles.alertTitle}>Needs attention — {highPriority.length} doc{highPriority.length !== 1 ? "s" : ""}</p>
          {highPriority.map((r) => (
            <p key={r.id} className={styles.alertRow}>
              <span className={styles.alertFile}>{r.filename}</span>
              <span className={styles.alertReason}>
                {[r.confidentiality?.label === "restricted" && "restricted", r.importance_level?.label === "high" && "high importance"].filter(Boolean).join(", ")}
              </span>
            </p>
          ))}
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
