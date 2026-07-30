import { useEffect, useRef, useState } from "react";
import styles from "./WatchStatus.module.css";

const BASE = import.meta.env.DEV ? "/api" : "";

const POLL_MS = 10_000;

interface RecentItem {
  filename: string;
  status: "classified" | "error";
  detail: string;
  at: string;
}

interface WatchStatusResponse {
  running: boolean;
  watch_dir: string;
  last_scan: string | null;
  processed_count: number;
  recent: RecentItem[];
  pending_hint: number;
}

function formatTime(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/**
 * Watched-folder control and live status.
 *
 * Point DocInfo at a directory and new PDF/DOCX/TXT files dropped there are
 * classified automatically, appearing in History like any upload. Polls the
 * status endpoint on the same cadence the backend polls the folder.
 *
 * The cost warning is deliberate and always visible: every new file spends the
 * same LLM credits as a manual upload, unattended and without confirmation.
 */
export function WatchStatus() {
  const [status, setStatus] = useState<WatchStatusResponse | null>(null);
  const [dir, setDir] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Don't clobber what the user is typing when a poll lands mid-edit. Only the
  // first successful load seeds the input.
  const seeded = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`${BASE}/watch/status`);
        if (!res.ok) throw new Error(`Status unavailable (${res.status})`);
        const data: WatchStatusResponse = await res.json();
        if (cancelled) return;
        setStatus(data);
        setLoadError(null);
        if (!seeded.current) {
          setDir(data.watch_dir);
          seeded.current = true;
        }
      } catch (e) {
        if (cancelled) return;
        setLoadError(e instanceof Error ? e.message : "Status unavailable");
      }
    }

    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${BASE}/watch/dir`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: dir.trim() }),
      });
      const data = await res.json();
      if (!res.ok) {
        // 422 carries a human-readable reason (bad path, not a folder).
        throw new Error(typeof data?.detail === "string" ? data.detail : `Save failed (${res.status})`);
      }
      setStatus(data as WatchStatusResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const configured = Boolean(status?.watch_dir);
  const active = Boolean(status?.running) && configured;

  return (
    <div className={styles.section}>
      <p className={styles.sectionLabel}>Watched Folder</p>

      <p className={styles.blurb}>
        New PDF, DOCX, and TXT files added to this folder are classified automatically and
        appear in History. Each new file costs the same as a manual upload.
      </p>

      <div className={styles.row}>
        <input
          className={styles.input}
          type="text"
          value={dir}
          placeholder="C:\exports\client-docs — leave blank to disable"
          spellCheck={false}
          onChange={(e) => setDir(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
          }}
          disabled={saving}
        />
        <button className={styles.saveBtn} onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>

      {error && <div className={styles.error}>{error}</div>}

      {loadError && !status && <p className={styles.muted}>{loadError}</p>}

      {status && (
        <div className={styles.statusLine}>
          <span className={active ? styles.dotActive : styles.dotIdle} aria-hidden="true" />
          <span className={styles.statusText}>
            {!configured
              ? "Idle — no folder configured"
              : active
                ? "Watching"
                : "Stopped"}
          </span>
          <span className={styles.meta}>last scan {formatTime(status.last_scan)}</span>
          <span className={styles.meta}>
            {status.processed_count} classified
          </span>
          {status.pending_hint > 0 && (
            <span className={styles.meta}>
              {status.pending_hint} pending
            </span>
          )}
        </div>
      )}

      {status && status.recent.length > 0 && (
        <ul className={styles.list}>
          {status.recent.map((item, i) => (
            <li key={`${item.at}-${i}`} className={styles.item} title={item.detail || undefined}>
              <span className={styles.filename}>{item.filename}</span>
              <span
                className={item.status === "error" ? styles.tagError : styles.tagOk}
              >
                {item.status}
              </span>
              <span className={styles.itemTime}>{formatTime(item.at)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
