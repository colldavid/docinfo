import { useEffect, useState } from "react";
import { Classify } from "./pages/Classify";
import { History } from "./pages/History";
import { NeedsReview } from "./pages/NeedsReview";
import { Portfolios } from "./pages/Portfolios";
import { About } from "./pages/About";
import { Login } from "./pages/Login";
import { Settings } from "./pages/Settings";
import styles from "./App.module.css";

const BASE = import.meta.env.DEV ? "/api" : "";

type Page = "classify" | "history" | "portfolios" | "review" | "about" | "settings";

const NAV: { id: Page; label: string }[] = [
  { id: "classify", label: "Classify" },
  { id: "history", label: "History" },
  { id: "portfolios", label: "Portfolios" },
  { id: "review", label: "Needs Review" },
  { id: "about", label: "How it Works" },
  { id: "settings", label: "Settings" },
];

interface AuthStatus {
  enabled: boolean;
  authenticated: boolean;
}

function App() {
  const [page, setPage] = useState<Page>("classify");
  const [auth, setAuth] = useState<AuthStatus | null>(null); // null = checking

  useEffect(() => {
    fetch(`${BASE}/auth/status`)
      .then((r) => r.json())
      .then(setAuth)
      // If the status check itself fails, fail open to the app shell — API
      // calls will still 401 individually rather than bricking the UI.
      .catch(() => setAuth({ enabled: false, authenticated: true }));
  }, []);

  async function handleLogout() {
    await fetch(`${BASE}/logout`, { method: "POST" });
    setAuth({ enabled: true, authenticated: false });
  }

  if (auth === null) return null; // brief check — no flash
  if (auth.enabled && !auth.authenticated) {
    return <Login onSuccess={() => setAuth({ enabled: true, authenticated: true })} />;
  }

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
        {auth.enabled && (
          <button className={styles.signOut} onClick={handleLogout}>Sign out</button>
        )}
      </nav>

      <main className={styles.main}>
        <div className={styles.content}>
          {page === "classify" && <Classify onShowAbout={() => setPage("about")} />}
          {page === "history" && <History />}
          {page === "portfolios" && <Portfolios />}
          {page === "review" && <NeedsReview />}
          {page === "about" && <About />}
          {page === "settings" && <Settings />}
        </div>
      </main>
    </div>
  );
}

export default App;
