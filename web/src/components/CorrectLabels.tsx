import { useState } from "react";
import styles from "./CorrectLabels.module.css";

const BASE = import.meta.env.DEV ? "/api" : "";

interface LabelVocabulary {
  doc_types: string[];
  industries: string[];
}

interface CorrectionResponse {
  id: number;
  doc_type: string | null;
  industry: string | null;
  user_doc_type: string | null;
  user_industry: string | null;
}

interface CorrectLabelsProps {
  recordId: number;
  docType: string | null;
  industry: string | null;
  userDocType?: string | null;
  userIndustry?: string | null;
}

/**
 * Human-in-the-loop label correction.
 *
 * Collapsed to a quiet text button so it never competes with the result itself;
 * expands into two selects driven by the model's own label vocabulary. The
 * model's prediction is never overwritten — corrections are stored beside it
 * and exported as training data by scripts/export_corrections.py.
 */
export function CorrectLabels({
  recordId,
  docType,
  industry,
  userDocType,
  userIndustry,
}: CorrectLabelsProps) {
  const [open, setOpen] = useState(false);
  const [vocab, setVocab] = useState<LabelVocabulary | null>(null);
  const [loadingVocab, setLoadingVocab] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Locally-held corrections so the UI stays truthful after saving without
  // needing the parent to refetch the record.
  const [correctedDocType, setCorrectedDocType] = useState<string | null>(userDocType ?? null);
  const [correctedIndustry, setCorrectedIndustry] = useState<string | null>(userIndustry ?? null);

  // Current select values. Prefilled with the correction if one exists,
  // otherwise the model's prediction.
  const [docTypeValue, setDocTypeValue] = useState<string>(userDocType ?? docType ?? "");
  const [industryValue, setIndustryValue] = useState<string>(userIndustry ?? industry ?? "");

  const hasCorrection = correctedDocType !== null || correctedIndustry !== null;

  async function expand() {
    setError(null);
    setSaved(false);
    // Re-seed the selects from the latest known state each time we open.
    setDocTypeValue(correctedDocType ?? docType ?? "");
    setIndustryValue(correctedIndustry ?? industry ?? "");
    setOpen(true);

    if (vocab || loadingVocab) return; // fetched once, then cached

    setLoadingVocab(true);
    try {
      const res = await fetch(`${BASE}/labels`);
      if (!res.ok) throw new Error(`Could not load labels (${res.status})`);
      setVocab((await res.json()) as LabelVocabulary);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load labels.");
    } finally {
      setLoadingVocab(false);
    }
  }

  function cancel() {
    setOpen(false);
    setError(null);
    setSaved(false);
  }

  async function save() {
    // Send only what actually changed relative to what's already stored.
    const baselineDocType = correctedDocType ?? docType ?? "";
    const baselineIndustry = correctedIndustry ?? industry ?? "";

    const body: { doc_type?: string; industry?: string } = {};
    if (docTypeValue && docTypeValue !== baselineDocType) body.doc_type = docTypeValue;
    if (industryValue && industryValue !== baselineIndustry) body.industry = industryValue;

    if (Object.keys(body).length === 0) {
      setOpen(false);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${BASE}/results/${recordId}/labels`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        let detail = `Save failed (${res.status})`;
        try {
          const err = await res.json();
          if (typeof err?.detail === "string") detail = err.detail;
        } catch {
          /* non-JSON error body — keep the status message */
        }
        throw new Error(detail);
      }
      const data = (await res.json()) as CorrectionResponse;
      setCorrectedDocType(data.user_doc_type);
      setCorrectedIndustry(data.user_industry);
      setSaved(true);
      setOpen(false);
      window.setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <div className={styles.wrapper}>
        <button
          type="button"
          className={hasCorrection ? styles.correctedBtn : styles.triggerBtn}
          onClick={expand}
        >
          {saved ? "saved" : hasCorrection ? "corrected ✓" : "correct labels"}
        </button>
        {hasCorrection && (
          <span className={styles.correctedSummary}>
            {correctedDocType && <>doc type: {correctedDocType}</>}
            {correctedDocType && correctedIndustry && <> · </>}
            {correctedIndustry && <>industry: {correctedIndustry}</>}
          </span>
        )}
        {error && <p className={styles.error}>{error}</p>}
      </div>
    );
  }

  return (
    <div className={styles.wrapper}>
      <div className={styles.panel}>
        {loadingVocab && <p className={styles.loading}>Loading labels…</p>}

        {vocab && (
          <div className={styles.fields}>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>Doc type</span>
              <select
                className={styles.select}
                value={docTypeValue}
                onChange={(e) => setDocTypeValue(e.target.value)}
                disabled={saving}
              >
                {!docTypeValue && <option value="">— select —</option>}
                {vocab.doc_types.map((d) => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>
            </label>

            <label className={styles.field}>
              <span className={styles.fieldLabel}>Industry</span>
              <select
                className={styles.select}
                value={industryValue}
                onChange={(e) => setIndustryValue(e.target.value)}
                disabled={saving}
              >
                {!industryValue && <option value="">— select —</option>}
                {vocab.industries.map((i) => (
                  <option key={i} value={i}>{i}</option>
                ))}
              </select>
            </label>
          </div>
        )}

        {error && <p className={styles.error}>{error}</p>}

        <div className={styles.actions}>
          <button
            type="button"
            className={styles.saveBtn}
            onClick={save}
            disabled={saving || !vocab}
          >
            {saving ? "saving…" : "Save"}
          </button>
          <button
            type="button"
            className={styles.cancelBtn}
            onClick={cancel}
            disabled={saving}
          >
            Cancel
          </button>
        </div>

        <p className={styles.hint}>
          Corrections are stored alongside the model's prediction and feed the next retraining run.
        </p>
      </div>
    </div>
  );
}
