import { useState, useEffect } from "react";
import { Classify } from "./pages/Classify";
import { History } from "./pages/History";
import { NeedsReview } from "./pages/NeedsReview";
import { Portfolios } from "./pages/Portfolios";
import { About } from "./pages/About";
import { Login } from "./pages/Login";
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
  const [authed, setAuthed] = useState<boolean | null>(null); // null = checking

  // Check if already authenticated on mount
  useEffect(() => {
    fetch("/results?limit=1")
      .then((r) => setAuthed(r.status !== 401))
      .catch(() => setAuthed(false));
  }, []);

  async function handleLogout() {
    await fetch("/logout", { method: "POST" });
    setAuthed(false);
  }

  if (authed === null) return null; // brief check — no flash
  if (!authed) return <Login onSuccess={() => setAuthed(true)} />;

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
        <button className={styles.signOut} onClick={handleLogout}>Sign out</button>
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
