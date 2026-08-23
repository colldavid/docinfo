import { useEffect, useRef, useState } from "react";
import type { ClassificationRecord, Portfolio } from "../types";
import { ResultsTable } from "./ResultsTable";
import { ResultDetail } from "./ResultDetail";
import { Contradictions } from "./Contradictions";
import { EntitiesTimeline } from "./EntitiesTimeline";
import { AskPortfolio } from "./AskPortfolio";
import { addRecordsToPortfolio, deliverableUrl, exportCsvUrl, fetchResults } from "../api";
import styles from "./PortfolioView.module.css";

interface Props {
  portfolio: Portfolio;
  records: ClassificationRecord[];
  /** Called after documents are added so the parent can refetch the portfolio. */
  onChanged?: () => void;
}

function AddDocsPanel({ portfolioId, onDone, onCancel }: {
  portfolioId: number;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [candidates, setCandidates] = useState<ClassificationRecord[] | null>(null);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Only documents not already in a portfolio are offered.
    fetchResults()
      .then((rs) => setCandidates(rs.filter((r) => r.portfolio_id == null && !r.error)))
      .catch(() => setCandidates([]));
  }, []);

  function toggle(id: number) {
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleAdd() {
    setBusy(true);
    setError(null);
    try {
      await addRecordsToPortfolio(portfolioId, [...chosen]);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Adding documents failed.");
      setBusy(false);
    }
  }

  return (
    <div className={styles.addPanel}>
      {candidates === null && <p className={styles.addHint}>Loading unassigned documents…</p>}
      {candidates !== null && candidates.length === 0 && (
        <p className={styles.addHint}>
          No unassigned documents. Classify new files first — documents already in a portfolio can't be moved.
        </p>
      )}
      {candidates !== null && candidates.length > 0 && (
        <>
          <div className={styles.addList}>
            {candidates.map((r) => (
              <label key={r.id} className={styles.addRow}>
                <input
                  type="checkbox"
                  checked={chosen.has(r.id)}
                  onChange={() => toggle(r.id)}
                  disabled={busy}
                />
                <span className={styles.addFilename}>{r.filename}</span>
                <span className={styles.addMeta}>{r.document_type?.label ?? "—"}</span>
              </label>
            ))}
          </div>
          {error && <p className={styles.addError}>{error}</p>}
          <div className={styles.addActions}>
            <button className={styles.addConfirmBtn} onClick={handleAdd} disabled={busy || chosen.size === 0}>
              {busy ? "Adding & re-analyzing…" : `Add ${chosen.size || ""} document${chosen.size === 1 ? "" : "s"}`}
            </button>
            <button className={styles.addCancelBtn} onClick={onCancel} disabled={busy}>Cancel</button>
          </div>
          {busy && (
            <p className={styles.addHint}>
              Theme, contradictions, and entities are being recomputed — this takes a few seconds.
            </p>
          )}
        </>
      )}
    </div>
  );
}

export function PortfolioView({ portfolio, records, onChanged }: Props) {
  const [selected, setSelected] = useState<ClassificationRecord | null>(null);
  const [adding, setAdding] = useState(false);
  const detailRef = useRef<HTMLDivElement>(null);

  function handleSelect(r: ClassificationRecord) {
    setSelected((prev) => prev?.id === r.id ? null : r);
    setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }

  const confCounts = tally(records, (r) => r.confidentiality?.label);
  const impCounts = tally(records, (r) => r.importance_level?.label);
  const allPain = records.flatMap((r) => r.pain_points);
  const painByCategory = topN(allPain, (p) => p.category || "Operations & Process", 7);
  const totalPain = allPain.length;
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
          {onChanged && (
            <button className={styles.exportBtn} onClick={() => setAdding((v) => !v)}>
              {adding ? "Close" : "Add documents"}
            </button>
          )}
          <a href={deliverableUrl(portfolio.id)} target="_blank" rel="noopener" className={styles.exportBtn}>Deliverable (PDF)</a>
          <a href={exportCsvUrl(portfolio.id)} download className={styles.exportBtn}>Export CSV</a>
        </div>
      </div>

      {adding && (
        <AddDocsPanel
          portfolioId={portfolio.id}
          onDone={() => { setAdding(false); onChanged?.(); }}
          onCancel={() => setAdding(false)}
        />
      )}

      {/* Theme */}
      {portfolio.theme && (
        <div className={styles.theme}>
          <p className={styles.themeLabel}>Key Themes</p>
          <ThemeContent theme={portfolio.theme} />
        </div>
      )}

      {/* Stats grid */}
      <div className={styles.statsGrid}>
        <StatGroup label="Confidentiality" items={confCounts} colorFn={confColor} />
        <StatGroup label="Importance" items={impCounts} colorFn={impColor} />
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

      {/* Pain points by theme — full width bar chart */}
      {painByCategory.length > 0 && (
        <div className={styles.painThemes}>
          <p className={styles.painThemesLabel}>
            Pain Points by Theme
            <span className={styles.painThemesTotal}>{totalPain} across {records.length} doc{records.length !== 1 ? "s" : ""}</span>
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

      {/* Cross-document analysis */}
      <Contradictions portfolioId={portfolio.id} />
      <EntitiesTimeline portfolioId={portfolio.id} />
      <AskPortfolio portfolioId={portfolio.id} />

      {/* Table */}
      <ResultsTable records={records} onSelect={handleSelect} selectedId={selected?.id} />

      {selected && (
        <div className={styles.detailCard} ref={detailRef}>
          <p className={styles.detailFilename}>{selected.filename}</p>
          <ResultDetail record={selected} />
        </div>
      )}
    </div>
  );
}

function ThemeContent({ theme }: { theme: string }) {
  // New format: JSON array of {headline, detail}. Old format: plain prose.
  let themes: { headline: string; detail: string }[] | null = null;
  try {
    const parsed = JSON.parse(theme);
    if (Array.isArray(parsed) && parsed.every((t) => t && typeof t.detail === "string")) {
      themes = parsed;
    }
  } catch {
    themes = null;
  }

  if (!themes) {
    return <p className={styles.themeText}>{theme}</p>;
  }

  return (
    <ul className={styles.themeList}>
      {themes.map((t, i) => (
        <li key={i} className={styles.themeItem}>
          {t.headline && <span className={styles.themeHeadline}>{t.headline}</span>}
          <span className={styles.themeDetail}>{t.detail}</span>
        </li>
      ))}
    </ul>
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
