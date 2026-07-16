import styles from "./About.module.css";

const PIPELINE_STEPS = [
  {
    num: "01",
    label: "Ingest",
    desc: "Upload PDF, DOCX, or TXT. Drag a folder to batch-process an entire client document package.",
  },
  {
    num: "02",
    label: "Classify",
    desc: "Sentence-transformer embeddings + logistic regression identify doc type (97.1% accuracy) and industry (94.5% accuracy) in milliseconds.",
  },
  {
    num: "03",
    label: "Extract",
    desc: "Claude Haiku reads each document and surfaces operational pain points — explicit and implicit — using the detected industry as context.",
  },
  {
    num: "04",
    label: "Score",
    desc: "Confidentiality level and strategic importance are assessed by LLM with consistency checks, returning a label, rationale, and confidence score.",
  },
  {
    num: "05",
    label: "Synthesize",
    desc: "Across a portfolio, cross-document themes are synthesized into a 2-3 sentence brief — the equivalent of a pre-read summary for the engagement team.",
  },
];

const COMPARISON = [
  {
    dimension: "Time to triage 10 documents",
    manual: "2–3 hours",
    docinfo: "< 30 seconds",
    highlight: true,
  },
  {
    dimension: "Pain point detection",
    manual: "Depends on analyst experience and fatigue",
    docinfo: "Consistent extraction across all docs, all industries",
    highlight: false,
  },
  {
    dimension: "Confidentiality flagging",
    manual: "Ad hoc — easy to miss",
    docinfo: "Every document scored with rationale",
    highlight: false,
  },
  {
    dimension: "Cross-document synthesis",
    manual: "Manual synthesis — hours of senior analyst time",
    docinfo: "Automatic portfolio theme in seconds",
    highlight: true,
  },
  {
    dimension: "Audit trail",
    manual: "Analyst notes, if they exist",
    docinfo: "Full classification history, exportable CSV",
    highlight: false,
  },
  {
    dimension: "Consistency across engagements",
    manual: "Varies by team and individual",
    docinfo: "Same pipeline every time",
    highlight: false,
  },
  {
    dimension: "Scales with doc volume",
    manual: "Linear — more docs, more hours",
    docinfo: "Parallel — 50 docs takes the same time as 5",
    highlight: true,
  },
];

const STATS = [
  { value: "97.1%", label: "Doc type accuracy" },
  { value: "94.5%", label: "Industry accuracy" },
  { value: "~15×", label: "Faster than manual triage" },
  { value: "3,700+", label: "Training documents" },
];

export function About() {
  return (
    <div className={styles.page}>
      {/* Hero */}
      <div className={styles.hero}>
        <div className={styles.accentLine} />
        <h1 className={styles.title}>What is DocInfo?</h1>
        <p className={styles.subtitle}>
          An AI-powered document intelligence layer for consulting teams. Drop in a client
          document package and get structured intelligence — doc type, industry, pain points,
          confidentiality, importance, and cross-document themes — in seconds, not hours.
        </p>
      </div>

      {/* Stats bar */}
      <div className={styles.statsBar}>
        {STATS.map((s) => (
          <div key={s.label} className={styles.stat}>
            <span className={styles.statValue}>{s.value}</span>
            <span className={styles.statLabel}>{s.label}</span>
          </div>
        ))}
      </div>

      {/* Pipeline */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>How it works</h2>
        <div className={styles.pipeline}>
          {PIPELINE_STEPS.map((step, i) => (
            <div key={step.num} className={styles.step}>
              <div className={styles.stepLeft}>
                <span className={styles.stepNum}>{step.num}</span>
                {i < PIPELINE_STEPS.length - 1 && <div className={styles.stepLine} />}
              </div>
              <div className={styles.stepRight}>
                <p className={styles.stepLabel}>{step.label}</p>
                <p className={styles.stepDesc}>{step.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Comparison */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>DocInfo vs. manual review</h2>
        <div className={styles.table}>
          <div className={styles.tableHeader}>
            <span className={styles.colDimension} />
            <span className={styles.colManual}>Manual Process</span>
            <span className={styles.colDocinfo}>DocInfo</span>
          </div>
          {COMPARISON.map((row) => (
            <div
              key={row.dimension}
              className={`${styles.tableRow} ${row.highlight ? styles.tableRowHighlight : ""}`}
            >
              <span className={styles.colDimension}>{row.dimension}</span>
              <span className={styles.colManual}>{row.manual}</span>
              <span className={styles.colDocinfo}>{row.docinfo}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Tech note */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Under the hood</h2>
        <div className={styles.techGrid}>
          <TechCard
            label="Embedding model"
            value="all-MiniLM-L6-v2"
            note="Sentence-transformers — fast, runs on CPU, fine-tunable"
          />
          <TechCard
            label="Classifiers"
            value="Logistic Regression"
            note="Trained on 3,700+ labeled consulting documents across 24 industries"
          />
          <TechCard
            label="LLM (extraction)"
            value="Claude Haiku"
            note="Pain points, confidentiality, importance, summaries, portfolio themes"
          />
          <TechCard
            label="Backend"
            value="FastAPI + SQLite"
            note="Parallel pipeline — all classifiers run concurrently per document"
          />
        </div>
      </section>
    </div>
  );
}

function TechCard({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className={styles.techCard}>
      <p className={styles.techLabel}>{label}</p>
      <p className={styles.techValue}>{value}</p>
      <p className={styles.techNote}>{note}</p>
    </div>
  );
}
