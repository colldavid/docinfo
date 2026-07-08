"""
DocInfo Streamlit UI.

Pages:
  Classify    — upload a document, pick industry, run pipeline
  History     — table of all past classification results
  Needs Review — documents flagged for human review

Run from project root:
  PYTHONPATH=. .venv/Scripts/streamlit run ui/app.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile
import streamlit as st
import pandas as pd
from datetime import datetime

from app.classify import classify_document
from app.database import ClassificationRecord, get_session, init_db
from app.ingestion import parse_document

# ── Bootstrap ────────────────────────────────────────────────────────────────
init_db()

st.set_page_config(page_title="DocInfo", page_icon="📄", layout="wide")

INDUSTRIES = [
    "", "technology", "healthcare", "finance", "energy", "retail",
    "manufacturing", "real_estate", "telecommunications", "defense",
    "media", "transportation", "education",
]

CONFIDENTIALITY_COLORS = {
    "public": "🟢",
    "internal": "🔵",
    "confidential": "🟠",
    "restricted": "🔴",
}

IMPORTANCE_COLORS = {
    "low": "⬇️",
    "medium": "➡️",
    "high": "⬆️",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_records(needs_review_only: bool = False) -> list[ClassificationRecord]:
    with get_session() as session:
        q = session.query(ClassificationRecord).order_by(
            ClassificationRecord.classified_at.desc()
        )
        if needs_review_only:
            q = q.filter(
                (ClassificationRecord.confidentiality_needs_review == True)
                | (ClassificationRecord.importance_needs_review == True)
            )
        records = q.all()
        # Detach from session so we can use them outside
        session.expunge_all()
        return records


def records_to_df(records: list[ClassificationRecord]) -> pd.DataFrame:
    rows = []
    for r in records:
        rows.append({
            "ID": r.id,
            "File": r.filename,
            "Doc Type": r.doc_type_label or "—",
            "Industry": r.industry or "—",
            "Confidentiality": f"{CONFIDENTIALITY_COLORS.get(r.confidentiality_label, '⚪')} {r.confidentiality_label or '—'}",
            "Importance": f"{IMPORTANCE_COLORS.get(r.importance_label, '')} {r.importance_label or '—'}",
            "Needs Review": "⚠️ Yes" if (r.confidentiality_needs_review or r.importance_needs_review) else "No",
            "Classified At": r.classified_at.strftime("%Y-%m-%d %H:%M") if r.classified_at else "—",
        })
    return pd.DataFrame(rows)


def show_result_detail(r: ClassificationRecord):
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Document Type", r.doc_type_label or "—",
                  delta=f"{r.doc_type_probability:.0%} confidence" if r.doc_type_probability else None,
                  delta_color="off")
    with col2:
        label = r.confidentiality_label or "—"
        icon = CONFIDENTIALITY_COLORS.get(r.confidentiality_label, "⚪")
        st.metric("Confidentiality", f"{icon} {label}")
    with col3:
        label = r.importance_label or "—"
        icon = IMPORTANCE_COLORS.get(r.importance_label, "")
        st.metric("Importance", f"{icon} {label}")

    if r.industry:
        st.caption(f"Industry: **{r.industry}**")

    if r.pain_points:
        st.markdown("**Pain Points Detected**")
        for pp in r.pain_points:
            score = pp.get("similarity_score", 0)
            st.progress(score, text=f"{pp['label']}  ({score:.2f})")
    else:
        st.info("No pain points detected above threshold.")

    with st.expander("Confidentiality rationale"):
        st.write(r.confidentiality_rationale or "—")
        if r.confidentiality_needs_review:
            st.warning("⚠️ Flagged for review (low confidence)")

    with st.expander("Importance rationale"):
        st.write(r.importance_rationale or "—")
        if r.importance_needs_review:
            st.warning("⚠️ Flagged for review (low confidence)")

    if r.error:
        st.error(f"Pipeline error: {r.error}")


# ── Sidebar nav ───────────────────────────────────────────────────────────────

st.sidebar.title("📄 DocInfo")
page = st.sidebar.radio("", ["Classify", "History", "Needs Review"])
st.sidebar.markdown("---")
st.sidebar.caption("Document intelligence for consulting.")


# ── Pages ─────────────────────────────────────────────────────────────────────

if page == "Classify":
    st.title("Classify a Document")

    uploaded = st.file_uploader(
        "Upload a document", type=["pdf", "docx", "txt"],
        help="Supported formats: PDF, Word (.docx), plain text"
    )
    industry = st.selectbox(
        "Industry (optional)",
        options=INDUSTRIES,
        format_func=lambda x: x.replace("_", " ").title() if x else "— Select industry —",
    )

    if uploaded and st.button("Classify", type="primary"):
        suffix = Path(uploaded.name).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(uploaded.read())
            tmp_path = Path(tmp.name)

        with st.spinner("Running classification pipeline…"):
            try:
                text = parse_document(tmp_path)
                if not text or not text.strip():
                    st.error("Could not extract text from this document.")
                    st.stop()

                result = classify_document(
                    tmp_path.with_name(uploaded.name),
                    text,
                    industry=industry or None,
                )

                # Persist
                with get_session() as session:
                    record = ClassificationRecord(
                        filename=result.filename,
                        classified_at=result.classified_at,
                        doc_type_label=result.document_type.label if result.document_type else None,
                        doc_type_probability=result.document_type.probability if result.document_type else None,
                        industry=result.industry,
                        pain_points=[
                            {"label": p.label, "similarity_score": p.similarity_score}
                            for p in result.pain_points
                        ],
                        confidentiality_label=result.confidentiality.label if result.confidentiality else None,
                        confidentiality_rationale=result.confidentiality.rationale if result.confidentiality else None,
                        confidentiality_confidence=result.confidentiality.confidence if result.confidentiality else None,
                        confidentiality_needs_review=result.confidentiality.needs_review if result.confidentiality else None,
                        importance_label=result.importance_level.label if result.importance_level else None,
                        importance_rationale=result.importance_level.rationale if result.importance_level else None,
                        importance_confidence=result.importance_level.confidence if result.importance_level else None,
                        importance_needs_review=result.importance_level.needs_review if result.importance_level else None,
                        error=result.error,
                    )
                    session.add(record)
                    session.commit()
                    session.refresh(record)
                    session.expunge(record)

                st.success(f"✅ Classified **{uploaded.name}**")
                show_result_detail(record)

            except Exception as e:
                st.error(f"Classification failed: {e}")
            finally:
                tmp_path.unlink(missing_ok=True)


elif page == "History":
    st.title("Classification History")

    records = load_records()
    if not records:
        st.info("No documents classified yet. Go to **Classify** to get started.")
    else:
        df = records_to_df(records)
        st.caption(f"{len(records)} document(s) classified")

        selected = st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        if selected and selected.selection.rows:
            row_idx = selected.selection.rows[0]
            chosen = records[row_idx]
            st.divider()
            st.subheader(f"📄 {chosen.filename}")
            show_result_detail(chosen)


elif page == "Needs Review":
    st.title("⚠️ Needs Review")
    st.caption("Documents where the pipeline had low confidence and flagged for human review.")

    records = load_records(needs_review_only=True)
    if not records:
        st.success("No documents currently flagged for review.")
    else:
        st.warning(f"{len(records)} document(s) need review")
        df = records_to_df(records)

        selected = st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        if selected and selected.selection.rows:
            row_idx = selected.selection.rows[0]
            chosen = records[row_idx]
            st.divider()
            st.subheader(f"📄 {chosen.filename}")
            show_result_detail(chosen)
