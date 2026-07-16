import { useRef, useState, useEffect } from "react";
import { classifyOne, createPortfolio, exportCsvUrl } from "../api";
import type { ClassificationRecord, Portfolio } from "../types";
import { BatchSummary } from "../components/BatchSummary";
import { ResultsTable } from "../components/ResultsTable";
import { ResultDetail } from "../components/ResultDetail";
import { PortfolioView } from "../components/PortfolioView";
import styles from "./Classify.module.css";

// Estimated manual time per document in minutes (reading + noting key issues)
const MANUAL_MINUTES_PER_DOC = 15;

function ElapsedTimer({ startTime, fileCount }: { startTime: number; fileCount: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setElapsed((Date.now() - startTime) / 1000), 250);
    return () => clearInterval(id);
  }, [startTime]);
  return (
    <div className={styles.elapsedBanner}>
      <span className={styles.elapsedDot} />
      Analyzing {fileCount} document{fileCount !== 1 ? "s" : ""}…
      <span className={styles.elapsedTime}>{elapsed.toFixed(1)}s</span>
    </div>
  );
}

function TimeSavedBanner({ fileCount, elapsedSeconds, manualMinutes }: {
  fileCount: number; elapsedSeconds: number; manualMinutes: number;
}) {
  const saved = manualMinutes - elapsedSeconds / 60;
  const pct = Math.round((saved / manualMinutes) * 100);
  const displayElapsed = elapsedSeconds < 60
    ? `${elapsedSeconds.toFixed(1)}s`
    : `${(elapsedSeconds / 60).toFixed(1)}m`;
  return (
    <div className={styles.timeSaved}>
      <div className={styles.timeSavedMain}>
        <span className={styles.timeSavedNum}>{pct}%</span>
        <span className={styles.timeSavedLabel}>time saved</span>
      </div>
      <div className={styles.timeSavedDetail}>
        DocInfo analyzed {fileCount} document{fileCount !== 1 ? "s" : ""} in{" "}
        <strong>{displayElapsed}</strong> — estimated manual review time:{" "}
        <strong>{manualMinutes >= 60 ? `${(manualMinutes / 60).toFixed(1)} hrs` : `${manualMinutes} min`}</strong>
      </div>
    </div>
  );
}

const INDUSTRIES = [
  "technology", "healthcare", "finance", "energy", "retail",
  "manufacturing", "real_estate", "telecommunications", "defense",
  "media", "transportation", "education",
];

const SUPPORTED = [".pdf", ".docx", ".txt"];
const isSupported = (f: File) => SUPPORTED.some((ext) => f.name.toLowerCase().endsWith(ext));

export function Classify({ onShowAbout }: { onShowAbout?: () => void }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [industry, setIndustry] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<ClassificationRecord[]>([]);
  const [selected, setSelected] = useState<ClassificationRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [portfolioName, setPortfolioName] = useState("");
  const [savedPortfolio, setSavedPortfolio] = useState<Portfolio | null>(null);
  const [savingPortfolio, setSavingPortfolio] = useState(false);
  const [classifyStartTime, setClassifyStartTime] = useState<number | null>(null);
  const [classifyElapsed, setClassifyElapsed] = useState<number | null>(null);
  const [fileStatuses, setFileStatuses] = useState<Record<string, "pending" | "done" | "error">>({});

  function addFiles(incoming: FileList | null) {
    if (!incoming) return;
    const valid = Array.from(incoming).filter(isSupported);
    setFiles(valid);
    setResults([]);
    setSelected(null);
    setSavedPortfolio(null);
  }

  async function handleSavePortfolio() {
    if (!portfolioName.trim() || !results.length) return;
    setSavingPortfolio(true);
    try {
      const ids = results.map((r) => r.id).filter(Boolean) as number[];
      const p = await createPortfolio(portfolioName.trim(), ids);
      setSavedPortfolio({ ...p, records: results });
    } catch (e) {
      alert("Failed to save portfolio: " + (e instanceof Error ? e.message : e));
    } finally {
      setSavingPortfolio(false);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    addFiles(e.dataTransfer.files);
  }

  async function handleClassify() {
    if (!files.length) return;
    setLoading(true);
    setError(null);
    setClassifyElapsed(null);
    setResults([]);
    setSelected(null);
    // Initialize all files as pending
    const initialStatuses: Record<string, "pending" | "done" | "error"> = {};
    for (const f of files) initialStatuses[f.name] = "pending";
    setFileStatuses(initialStatuses);

    const t0 = Date.now();
    setClassifyStartTime(t0);
    try {
      const settled = await Promise.all(
        files.map(async (f) => {
          try {
            const r = await classifyOne(f, industry || null);
            setFileStatuses((prev) => ({ ...prev, [f.name]: "done" }));
            setResults((prev) => [...prev, r]);
            return r;
          } catch {
            setFileStatuses((prev) => ({ ...prev, [f.name]: "error" }));
            return null;
          }
        })
      );
      const succeeded = settled.filter(Boolean) as typeof results;
      if (succeeded.length === 0) throw new Error("All files failed to classify.");
      setClassifyElapsed((Date.now() - t0) / 1000);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.includes("fetch") || msg.includes("Failed")
        ? "Could not reach the API — is the FastAPI server running on port 8000?"
        : msg);
    } finally {
      setLoading(false);
      setClassifyStartTime(null);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.heroText}>
        <div className={styles.accentLine} />
        <h1 className={styles.title}>Classify Documents</h1>
        <p className={styles.subtitle}>
          Upload files or a folder to run the full intelligence pipeline.
          {onShowAbout && (
            <button className={styles.howItWorksLink} onClick={onShowAbout}>
              See how it works →
            </button>
          )}
        </p>
      </div>

      {/* Drop zone */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
        className={styles.dropZone}
      >
        <p className={styles.dropMain}>Drop files here</p>
        <p className={styles.dropSub}>PDF · DOCX · TXT</p>
        <div className={styles.dropActions}>
          <button onClick={() => fileInputRef.current?.click()} className={styles.dropBtn}>
            Select files
          </button>
          <button onClick={() => folderInputRef.current?.click()} className={styles.dropBtn}>
            Select folder
          </button>
        </div>
        <input ref={fileInputRef} type="file" multiple accept=".pdf,.docx,.txt" className={styles.hidden} onChange={(e) => addFiles(e.target.files)} />
        {/* separate input for folder — webkitdirectory can't coexist with accept on all browsers */}
        <input ref={folderInputRef} type="file" multiple className={styles.hidden}
          // @ts-expect-error non-standard attribute
          webkitdirectory=""
          onChange={(e) => addFiles(e.target.files)}
        />
      </div>

      {files.length > 0 && (
        <div className={styles.fileList}>
          <span className={styles.fileCount}>{files.length} file{files.length !== 1 ? "s" : ""} selected</span>
          <div className={styles.filePills}>
            {files.map((f) => {
              const status = fileStatuses[f.name];
              return (
                <span
                  key={f.name}
                  className={`${styles.pill} ${status === "done" ? styles.pillDone : status === "error" ? styles.pillError : status === "pending" ? styles.pillPending : ""}`}
                >
                  {status === "done" ? "✓ " : status === "error" ? "✗ " : status === "pending" ? "⋯ " : ""}
                  {f.name}
                </span>
              );
            })}
          </div>
          {!loading && <button onClick={() => { setFiles([]); setResults([]); setFileStatuses({}); }} className={styles.clearBtn}>clear</button>}
        </div>
      )}

      <div className={styles.controls}>
        <div className={styles.selectWrap}>
          <label className={styles.selectLabel}>Industry</label>
          <select value={industry} onChange={(e) => setIndustry(e.target.value)} className={styles.select}>
            <option value="">— optional —</option>
            {INDUSTRIES.map((i) => <option key={i} value={i}>{i.replace("_", " ")}</option>)}
          </select>
        </div>
        <button onClick={handleClassify} disabled={!files.length || loading} className={styles.classifyBtn}>
          {loading ? "Classifying…" : `Classify${files.length > 1 ? ` (${files.length})` : ""}`}
        </button>
      </div>

      {error && <div className={styles.error}>{error}</div>}

      {loading && classifyStartTime && (
        <ElapsedTimer startTime={classifyStartTime} fileCount={files.length} />
      )}

      {results.length > 0 && classifyElapsed !== null && (
        <TimeSavedBanner
          fileCount={results.length}
          elapsedSeconds={classifyElapsed}
          manualMinutes={results.length * MANUAL_MINUTES_PER_DOC}
        />
      )}

      {results.length > 0 && (
        <div className={styles.results}>
          {/* Save as portfolio */}
          {!savedPortfolio && (
            <div className={styles.savePortfolio}>
              <input
                className={styles.portfolioInput}
                placeholder="Portfolio name…"
                value={portfolioName}
                onChange={(e) => setPortfolioName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSavePortfolio()}
              />
              <button
                className={styles.savePortfolioBtn}
                onClick={handleSavePortfolio}
                disabled={!portfolioName.trim() || savingPortfolio}
              >
                {savingPortfolio ? "Saving…" : "Save as Portfolio"}
              </button>
              <a href={exportCsvUrl()} download className={styles.exportBtn}>Export CSV</a>
            </div>
          )}

          {/* Portfolio view if saved */}
          {savedPortfolio ? (
            <PortfolioView portfolio={savedPortfolio} records={results} />
          ) : (
            <>
              {results.length > 1 && <BatchSummary records={results} />}
              {results.length > 1 && <p className={styles.tableHint}>Click a row to see full details below.</p>}
              {results.length === 1 ? (
                <div className={styles.detailCard}>
                  <p className={styles.detailFilename}>{results[0].filename}</p>
                  <ResultDetail record={results[0]} />
                </div>
              ) : (
                <>
                  <ResultsTable records={results} onSelect={setSelected} selectedId={selected?.id} />
                  {selected && (
                    <div className={styles.detailCard}>
                      <p className={styles.detailFilename}>{selected.filename}</p>
                      <ResultDetail record={selected} />
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
