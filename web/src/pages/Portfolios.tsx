import { useEffect, useState } from "react";
import { fetchPortfolios, fetchPortfolio, deletePortfolio } from "../api";
import type { Portfolio } from "../types";
import { PortfolioView } from "../components/PortfolioView";
import styles from "./Page.module.css";

export function Portfolios() {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [selected, setSelected] = useState<Portfolio | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPortfolios()
      .then(setPortfolios)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  async function handleSelect(p: Portfolio) {
    if (selected?.id === p.id) { setSelected(null); return; }
    const full = await fetchPortfolio(p.id);
    setSelected(full);
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this portfolio? The documents will remain in History.")) return;
    await deletePortfolio(id);
    setPortfolios((prev) => prev.filter((p) => p.id !== id));
    if (selected?.id === id) setSelected(null);
  }

  if (loading) return <p className={styles.muted}>Loading…</p>;
  if (error) return <div className={styles.error}>Error: {error}</div>;

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div className={styles.accentLine} />
        <h1 className={styles.title}>Portfolios</h1>
        <p className={styles.subtitle}>{portfolios.length} saved portfolio{portfolios.length !== 1 ? "s" : ""}</p>
      </div>

      {portfolios.length === 0 ? (
        <p className={styles.muted}>No portfolios yet. Classify a batch of documents and save them as a portfolio.</p>
      ) : (
        <div className={styles.portfolioList}>
          {portfolios.map((p) => (
            <div key={p.id} className={`${styles.portfolioRow} ${selected?.id === p.id ? styles.portfolioRowActive : ""}`}>
              <button className={styles.portfolioRowBtn} onClick={() => handleSelect(p)}>
                <span className={styles.portfolioRowName}>{p.name}</span>
                <span className={styles.portfolioRowMeta}>
                  {p.record_count} doc{p.record_count !== 1 ? "s" : ""}
                  {" · "}
                  {new Date(p.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
                </span>
              </button>
              {selected?.id === p.id && selected.records && (
                <div className={styles.portfolioDetail}>
                  <PortfolioView
                    portfolio={selected}
                    records={selected.records}
                    onDelete={() => handleDelete(p.id)}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
