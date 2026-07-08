import styles from "./Badge.module.css";

export function ConfidentialityBadge({ label }: { label: string }) {
  return <span className={`${styles.badge} ${styles[`conf_${label}`] ?? ""}`}>{label}</span>;
}

export function ImportanceBadge({ label }: { label: string }) {
  return <span className={`${styles.badge} ${styles[`imp_${label}`] ?? ""}`}>{label}</span>;
}
