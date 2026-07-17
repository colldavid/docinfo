export interface DocTypeResult {
  label: string;
  probability: number;
  needs_review: boolean;
}

export interface PainPoint {
  label: string;
  context?: string;
  question?: string;
  category?: string;
  similarity_score?: number;
}

export interface ConfidentialityResult {
  label: "public" | "sensitive" | "restricted";
  rationale: string;
  confidence: number;
  needs_review: boolean;
}

export interface ImportanceResult {
  label: "low" | "medium" | "high";
  rationale: string;
  confidence: number;
  needs_review: boolean;
}

export interface IndustryResult {
  label: string;
  probability: number;
  needs_review: boolean;
  user_provided: boolean;
}

export interface ClassificationRecord {
  id: number;
  filename: string;
  classified_at: string;
  document_type: DocTypeResult | null;
  industry: IndustryResult | null;
  pain_points: PainPoint[];
  confidentiality: ConfidentialityResult | null;
  importance_level: ImportanceResult | null;
  summary: string | null;
  error: string | null;
  portfolio_id?: number | null;
}

export interface Portfolio {
  id: number;
  name: string;
  created_at: string;
  theme: string | null;
  record_count: number;
  records?: ClassificationRecord[];
}
