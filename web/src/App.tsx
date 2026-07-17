import { useState } from "react";
import { Classify } from "./pages/Classify";
import { History } from "./pages/History";
import { NeedsReview } from "./pages/NeedsReview";
import { Portfolios } from "./pages/Portfolios";
import { About } from "./pages/About";
import styles from "./App.module.css";

type Page = "classify" | "history" | "portfolios" | "review" | "about";

const NAV: { id: Page; label: string }[] = [
  { id: "classify", label: "Classify" },
  { id: "history", label: "History" },
  { id: "portfolios", label: "Portfolios" },
  { id: "review", label: "Needs Review" },
  { id: "about", label: "How it Works" },
];

function App() {
  const [page, setPage] = useState<Page>("classify");

  return (
    <div className={styles.layout}>
      <nav className={styles.nav}>
        <button className={styles.logo} onClick={() => setPage("classify")}>DOCINFO</button>
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
          {page === "classify" && <Classify onShowAbout={() => setPage("about")} />}
          {page === "history" && <History />}
          {page === "portfolios" && <Portfolios />}
          {page === "review" && <NeedsReview />}
          {page === "about" && <About />}
        </div>
      </main>
    </div>
  );
}

export default App;
