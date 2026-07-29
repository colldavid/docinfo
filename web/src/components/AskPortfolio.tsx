import { useState } from "react";
import styles from "./AskPortfolio.module.css";

// In dev, Vite proxies /api → localhost:8000. In prod (served by FastAPI), no prefix needed.
const BASE = import.meta.env.DEV ? "/api" : "";

interface Source {
  record_id: number;
  filename: string;
  excerpt: string;
}

interface AskResponse {
  answer: string;
  sources: Source[];
}

interface Exchange {
  question: string;
  answer: string;
  sources: Source[];
}

export function AskPortfolio({ portfolioId }: { portfolioId: number }) {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleAsk() {
    const q = question.trim();
    if (!q || loading) return;

    setLoading(true);
    setError(null);
    setNotice(null);

    try {
      const res = await fetch(`${BASE}/portfolios/${portfolioId}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });

      if (!res.ok) {
        const detail = await readDetail(res);
        // 409 = portfolio has no indexed chunks. Informational, not a failure.
        if (res.status === 409) setNotice(detail);
        else setError(detail);
        return;
      }

      const data: AskResponse = await res.json();
      setHistory((prev) => [
        { question: q, answer: data.answer, sources: data.sources ?? [] },
        ...prev,
      ]);
      setQuestion("");
    } catch {
      setError("Could not reach the server.");
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAsk();
    }
  }

  return (
    <div className={styles.wrapper}>
      <p className={styles.label}>Ask This Portfolio</p>

      <div className={styles.inputRow}>
        <input
          type="text"
          className={styles.askInput}
          placeholder="Which documents mention covenant risk?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
        />
        <button
          type="button"
          className={styles.askBtn}
          onClick={handleAsk}
          disabled={loading || !question.trim()}
        >
          Ask
        </button>
      </div>

      {loading && <p className={styles.loading}>Searching documents…</p>}
      {notice && <p className={styles.notice}>{notice}</p>}
      {error && <p className={styles.error}>{error}</p>}

      {history.length > 0 && (
        <>
          <div className={styles.history}>
            {history.map((ex, i) => (
              <div key={history.length - i} className={styles.exchange}>
                <p className={styles.question}>{ex.question}</p>
                <div className={styles.answerCard}>
                  <p className={styles.answerText}>{ex.answer}</p>
                  {ex.sources.length > 0 && (
                    <div className={styles.sources}>
                      <span className={styles.sourcesLabel}>Sources</span>
                      <div className={styles.chips}>
                        {ex.sources.map((s) => (
                          <span
                            key={s.record_id}
                            className={styles.chip}
                            title={s.excerpt}
                          >
                            {s.filename}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
          <button
            type="button"
            className={styles.clearBtn}
            onClick={() => setHistory([])}
          >
            clear
          </button>
        </>
      )}
    </div>
  );
}

/** Pull FastAPI's {"detail": "..."} message out of an error response. */
async function readDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // fall through to generic message
  }
  return "Something went wrong. Please try again.";
}
