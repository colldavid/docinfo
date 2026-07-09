import { useState } from "react";
import { Classify } from "./pages/Classify";
import { History } from "./pages/History";
import { NeedsReview } from "./pages/NeedsReview";
import { Portfolios } from "./pages/Portfolios";
import styles from "./App.module.css";

type Page = "classify" | "history" | "portfolios" | "review";

const NAV: { id: Page; label: string }[] = [
  { id: "classify", label: "Classify" },
  { id: "history", label: "History" },
  { id: "portfolios", label: "Portfolios" },
  { id: "review", label: "Needs Review" },
];

function App() {
  const [page, setPage] = useState<Page>("classify");

  return (
    <div className={styles.layout}>
      <nav className={styles.nav}>
        <span className={styles.logo}>DOCINFO</span>
        <div className={styles.links}>
          {NAV.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setPage(id)}
              className={`${styles.link} ${page === id ? styles.active : ""}`}
            >
              {label}
            </button>
          ))}
        </div>
      </nav>

      <main className={styles.main}>
        <div className={styles.content}>
          {page === "classify" && <Classify />}
          {page === "history" && <History />}
          {page === "portfolios" && <Portfolios />}
          {page === "review" && <NeedsReview />}
        </div>
      </main>
    </div>
  );
}

export default App;
