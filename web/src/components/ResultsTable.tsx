import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  flexRender,
  createColumnHelper,
  type ColumnFiltersState,
} from "@tanstack/react-table";
import { useState } from "react";
import type { ClassificationRecord } from "../types";
import { ConfidentialityBadge, ImportanceBadge } from "./Badge";
import styles from "./ResultsTable.module.css";

const col = createColumnHelper<ClassificationRecord>();

const COLUMNS = [
  col.accessor("filename", { header: "File" }),
  col.accessor((r) => r.document_type?.label ?? "—", { id: "doc_type", header: "Doc Type" }),
  col.accessor("industry", { header: "Industry", cell: (i) => i.getValue() ?? "—" }),
  col.accessor((r) => r.confidentiality?.label ?? "—", {
    id: "confidentiality",
    header: "Confidentiality",
    cell: (i) => { const v = i.getValue(); return v !== "—" ? <ConfidentialityBadge label={v} /> : <span className={styles.dash}>—</span>; },
  }),
  col.accessor((r) => r.importance_level?.label ?? "—", {
    id: "importance",
    header: "Importance",
    cell: (i) => { const v = i.getValue(); return v !== "—" ? <ImportanceBadge label={v} /> : <span className={styles.dash}>—</span>; },
  }),
  col.accessor(
    (r) => r.confidentiality?.needs_review || r.importance_level?.needs_review ? "yes" : "no",
    { id: "needs_review", header: "Review", cell: (i) => i.getValue() === "yes" ? <span className={styles.flagged}>⚠ yes</span> : <span className={styles.dash}>—</span> }
  ),
  col.accessor((r) => r.classified_at.replace("T", " ").slice(0, 16), { id: "classified_at", header: "Classified At" }),
];

interface Props {
  records: ClassificationRecord[];
  onSelect?: (record: ClassificationRecord) => void;
  selectedId?: number;
}

export function ResultsTable({ records, onSelect, selectedId }: Props) {
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  const table = useReactTable({
    data: records,
    columns: COLUMNS,
    state: { columnFilters },
    onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const confValues = [...new Set(records.map((r) => r.confidentiality?.label).filter(Boolean))] as string[];
  const impValues = [...new Set(records.map((r) => r.importance_level?.label).filter(Boolean))] as string[];
  const typeValues = [...new Set(records.map((r) => r.document_type?.label).filter(Boolean))] as string[];

  function setFilter(id: string, value: string) {
    setColumnFilters((prev) => {
      const without = prev.filter((f) => f.id !== id);
      return value ? [...without, { id, value }] : without;
    });
  }

  function getFilter(id: string): string {
    return (columnFilters.find((f) => f.id === id)?.value as string) ?? "";
  }

  return (
    <div className={styles.wrapper}>
      <div className={styles.filters}>
        <FilterSelect label="Confidentiality" options={confValues} value={getFilter("confidentiality")} onChange={(v) => setFilter("confidentiality", v)} />
        <FilterSelect label="Importance" options={impValues} value={getFilter("importance")} onChange={(v) => setFilter("importance", v)} />
        <FilterSelect label="Doc Type" options={typeValues} value={getFilter("doc_type")} onChange={(v) => setFilter("doc_type", v)} />
        {columnFilters.length > 0 && (
          <button onClick={() => setColumnFilters([])} className={styles.clearBtn}>clear filters</button>
        )}
      </div>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              {table.getHeaderGroups()[0].headers.map((h) => (
                <th key={h.id} className={styles.th}>
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                onClick={() => onSelect?.(row.original)}
                className={`${styles.tr} ${row.original.id === selectedId ? styles.selected : ""}`}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className={styles.td}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {table.getRowModel().rows.length === 0 && (
          <p className={styles.empty}>No results match the current filters.</p>
        )}
      </div>
    </div>
  );
}

function FilterSelect({ label, options, value, onChange }: { label: string; options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={styles.select}>
      <option value="">All {label}</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}
