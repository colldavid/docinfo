import { useEffect, useState } from "react";
import { WatchStatus } from "../components/WatchStatus";
import styles from "./Settings.module.css";
import pageStyles from "./Page.module.css";

const BASE = import.meta.env.DEV ? "/api" : "";

type Provider = "anthropic" | "openai";

/** The runtime-settable values, as strings (the store is string-keyed). */
interface EffectiveSettings {
  llm_provider: string;
  llm_model: string;
  llm_base_url: string;
  doc_type_review_threshold: string;
  industry_review_threshold: string;
  watch_dir: string;
}

interface SettingsResponse {
  settings: EffectiveSettings;
  provider_keys: Record<Provider, boolean>;
  model_suggestions: Record<Provider, string[]>;
  cache_entries: number;
}

/** Only the fields this page owns — watch_dir is deliberately not rendered here. */
type EditableKey =
  | "llm_provider"
  | "llm_model"
  | "llm_base_url"
  | "doc_type_review_threshold"
  | "industry_review_threshold";

const EDITABLE_KEYS: EditableKey[] = [
  "llm_provider",
  "llm_model",
  "llm_base_url",
  "doc_type_review_threshold",
  "industry_review_threshold",
];

const THRESHOLD_KEYS = new Set<EditableKey>([
  "doc_type_review_threshold",
  "industry_review_threshold",
]);

const PROVIDER_LABELS: Record<Provider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
};

type Form = Record<EditableKey, string>;

function toForm(s: EffectiveSettings): Form {
  return {
    llm_provider: s.llm_provider,
    llm_model: s.llm_model,
    llm_base_url: s.llm_base_url,
    doc_type_review_threshold: s.doc_type_review_threshold,
    industry_review_threshold: s.industry_review_threshold,
  };
}

export function Settings() {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  // The last saved values, so "changed" is a real diff rather than a dirty flag.
  const [saved, setSaved] = useState<Form | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    fetch(`${BASE}/settings`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`Failed to load settings (${r.status})`);
        return (await r.json()) as SettingsResponse;
      })
      .then((d) => {
        setData(d);
        setForm(toForm(d.settings));
        setSaved(toForm(d.settings));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  function update(key: EditableKey, value: string) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
    setJustSaved(false);
  }

  const changedKeys: EditableKey[] =
    form && saved
      ? EDITABLE_KEYS.filter((k) => {
          // Thresholds are compared numerically: "0.20" and "0.2" are the same
          // setting, and the number input reformats freely as you type.
          if (THRESHOLD_KEYS.has(k)) {
            const a = parseFloat(form[k]);
            const b = parseFloat(saved[k]);
            if (!Number.isNaN(a) && !Number.isNaN(b)) return a !== b;
          }
          return form[k] !== saved[k];
        })
      : [];

  async function handleSave() {
    if (!form || changedKeys.length === 0) return;
    setSaving(true);
    setError(null);
    try {
      // Send only what actually changed — a PATCH of unchanged values would
      // pin .env-backed defaults into the override store for no reason.
      const body: Record<string, string | number> = {};
      for (const k of changedKeys) {
        body[k] = THRESHOLD_KEYS.has(k) ? parseFloat(form[k]) : form[k];
      }
      const res = await fetch(`${BASE}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await res.json().catch(() => null);
      if (!res.ok) {
        const detail = payload?.detail;
        throw new Error(
          typeof detail === "string" ? detail : `Save failed (${res.status})`
        );
      }
      const next = payload as EffectiveSettings;
      setForm(toForm(next));
      setSaved(toForm(next));
      setData((d) => (d ? { ...d, settings: next } : d));
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleClearCache() {
    if (
      !confirm(
        "Clear the analysis cache?\n\nEvery document analysed from now on will be re-run through the LLM, which uses API credits. This cannot be undone."
      )
    )
      return;
    setClearing(true);
    setError(null);
    try {
      const res = await fetch(`${BASE}/settings/clear-cache`, { method: "POST" });
      const payload = await res.json().catch(() => null);
      if (!res.ok) {
        const detail = payload?.detail;
        throw new Error(
          typeof detail === "string" ? detail : `Clear failed (${res.status})`
        );
      }
      setData((d) => (d ? { ...d, cache_entries: 0 } : d));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setClearing(false);
    }
  }

  if (loading) return <p className={pageStyles.muted}>Loading…</p>;
  if (!data || !form) {
    return <div className={pageStyles.error}>Error: {error ?? "Settings unavailable"}</div>;
  }

  const provider = (form.llm_provider === "openai" ? "openai" : "anthropic") as Provider;
  const suggestions = data.model_suggestions[provider] ?? [];

  return (
    <div className={pageStyles.page}>
      <div className={pageStyles.header}>
        <div className={pageStyles.accentLine} />
        <h1 className={pageStyles.title}>Settings</h1>
        <p className={pageStyles.subtitle}>
          Model choice, review thresholds, and cache maintenance. Changes take effect
          immediately — no restart.
        </p>
      </div>

      {error && <div className={pageStyles.error}>{error}</div>}

      {/* ── LLM ──────────────────────────────────────────────────────────── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>LLM</h2>
        <p className={styles.sectionNote}>
          Used for the reasoning steps only — confidentiality, importance, pain points,
          summaries and themes. Classification runs locally and is unaffected.
        </p>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="llm_provider">Provider</label>
          <select
            id="llm_provider"
            className={styles.select}
            value={form.llm_provider}
            onChange={(e) => update("llm_provider", e.target.value)}
          >
            {(Object.keys(PROVIDER_LABELS) as Provider[]).map((p) => {
              const hasKey = data.provider_keys[p];
              return (
                <option key={p} value={p} disabled={!hasKey}>
                  {PROVIDER_LABELS[p]}
                  {hasKey ? "" : " — no API key configured"}
                </option>
              );
            })}
          </select>
          {!data.provider_keys[provider] && (
            <p className={styles.warn}>
              No API key found for {PROVIDER_LABELS[provider]}. Set{" "}
              <code className={styles.code}>{provider.toUpperCase()}_API_KEY</code> in
              .env.local and restart to enable it.
            </p>
          )}
          <p className={styles.hint}>
            API keys are read from the environment and can't be changed here.
          </p>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="llm_model">Model</label>
          <input
            id="llm_model"
            className={styles.input}
            list="llm-model-suggestions"
            value={form.llm_model}
            onChange={(e) => update("llm_model", e.target.value)}
            placeholder="model id"
            spellCheck={false}
            autoComplete="off"
          />
          <datalist id="llm-model-suggestions">
            {suggestions.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <p className={styles.hint}>
            Suggestions only — any model id your provider accepts will work.
          </p>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="llm_base_url">Base URL</label>
          <input
            id="llm_base_url"
            className={styles.input}
            value={form.llm_base_url}
            onChange={(e) => update("llm_base_url", e.target.value)}
            placeholder="provider default — set for Azure/enterprise gateway"
            spellCheck={false}
            autoComplete="off"
          />
          <p className={styles.hint}>Leave blank to use the provider's own endpoint.</p>
        </div>
      </section>

      {/* ── Review thresholds ────────────────────────────────────────────── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Review thresholds</h2>
        <p className={styles.sectionNote}>
          Applied to rescaled classifier confidence, where 0 is a coin flip and 1 is
          certain. Raising a threshold sends more documents to Needs Review.
        </p>

        <div className={styles.thresholdGrid}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="doc_type_review_threshold">
              Document type
            </label>
            <input
              id="doc_type_review_threshold"
              className={styles.numberInput}
              type="number"
              step={0.05}
              min={0}
              max={1}
              value={form.doc_type_review_threshold}
              onChange={(e) => update("doc_type_review_threshold", e.target.value)}
            />
            <p className={styles.hint}>Flag for human review below this confidence.</p>
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="industry_review_threshold">
              Industry
            </label>
            <input
              id="industry_review_threshold"
              className={styles.numberInput}
              type="number"
              step={0.05}
              min={0}
              max={1}
              value={form.industry_review_threshold}
              onChange={(e) => update("industry_review_threshold", e.target.value)}
            />
            <p className={styles.hint}>Flag for human review below this confidence.</p>
          </div>
        </div>
      </section>

      <div className={styles.saveRow}>
        <button
          className={styles.saveBtn}
          onClick={handleSave}
          disabled={saving || changedKeys.length === 0}
        >
          {saving ? "Saving…" : "Save"}
        </button>
        {justSaved && <span className={styles.savedNote}>Saved ✓</span>}
        {!justSaved && changedKeys.length > 0 && (
          <span className={styles.dirtyNote}>
            {changedKeys.length} unsaved change{changedKeys.length !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* ── Maintenance ──────────────────────────────────────────────────── */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Maintenance</h2>
        <p className={styles.sectionNote}>
          Cached results guarantee identical output for identical documents. Clearing
          forces fresh analysis on next classify (uses API credits).
        </p>

        <div className={styles.cacheRow}>
          <span className={styles.cacheCount}>{data.cache_entries.toLocaleString()}</span>
          <span className={styles.cacheLabel}>
            cached analys{data.cache_entries === 1 ? "is" : "es"}
          </span>
        </div>

        <button
          className={styles.clearBtn}
          onClick={handleClearCache}
          disabled={clearing || data.cache_entries === 0}
        >
          {clearing ? "Clearing…" : "Clear analysis cache"}
        </button>
      </section>

      {/* Watched folder — owns the watch_dir setting end to end */}
      <WatchStatus />
    </div>
  );
}
