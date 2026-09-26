export interface User {
  id: string;
  name: string;
  email: string;
  created_at: string;
}

export interface Project {
  id: string;
  name: string;
  description: string | null;
  target_column: string | null;
  status: "draft" | "analyzing" | "completed" | "failed" | "archived";
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary extends Project {
  dataset_count: number;
  model_count: number;
  report_count: number;
}

export interface DatasetColumnInfo {
  name: string;
  dtype: string;
  semantic_type: string | null;
  missing_count: number;
  unique_count: number;
  is_numeric: boolean;
  is_categorical: boolean;
  is_datetime: boolean;
  is_id_like: boolean;
}

export interface Dataset {
  id: string;
  project_id: string;
  file_name: string;
  file_type: string;
  file_size: number;
  row_count: number | null;
  column_count: number | null;
  missing_values: number | null;
  duplicate_rows: number | null;
  is_cleaned: boolean;
  cleaning_version: number;
  created_at: string;
}

export interface ColumnStats {
  min: number | null;
  max: number | null;
  mean: number | null;
  median: number | null;
  std: number | null;
}

export interface ColumnProfile {
  name: string;
  dtype: string;
  semantic_type: string;
  missing_count: number;
  missing_pct: number;
  unique_count: number;
  is_numeric: boolean;
  is_categorical: boolean;
  is_datetime: boolean;
  is_id_like: boolean;
  stats?: ColumnStats;
  top_values?: { value: string; count: number }[];
}

export interface DatasetProfile {
  row_count: number;
  column_count: number;
  numeric_columns: string[];
  categorical_columns: string[];
  datetime_columns: string[];
  missing_values: number;
  duplicate_rows: number;
  columns: ColumnProfile[];
}

export interface TargetCandidate {
  column: string;
  score: number;
  reason: string;
  problem_type_hint: "classification" | "regression";
}

export interface DatasetDetail extends Dataset {
  profile_json: DatasetProfile | null;
  cleaning_log_json: Record<string, unknown> | null;
  columns: DatasetColumnInfo[];
  suggested_target_column: string | null;
  target_candidates: TargetCandidate[];
}

export interface QualityIssue {
  id: string;
  column: string | null;
  type: "duplicate_rows" | "missing_values" | "outliers" | "invalid_dates" | "impossible_values" | "mixed_types";
  severity: "low" | "medium" | "high";
  message: string;
  affected_count: number;
  recommended_fix: {
    action: "remove_duplicates" | "fill_missing" | "review_only";
    strategy?: "median" | "mode";
    reason?: string;
  };
}

export interface DatasetRow {
  row_index: number;
  values: Record<string, unknown>;
}

export interface EdaResult {
  summary: {
    rows: number;
    columns: number;
    numeric_columns: number;
    categorical_columns: number;
    missing_cells: number;
    duplicate_rows: number;
  };
  descriptive_stats: Record<string, Record<string, number | null>>;
  distributions: Record<string, { bins: number[]; counts: number[] }>;
  outliers: Record<string, { count: number; pct: number; lower_bound: number | null; upper_bound: number | null }>;
  category_frequency: Record<string, { value: string; count: number }[]>;
  correlation: { columns: string[]; matrix: number[][] };
  time_trends: Record<string, { labels: string[]; values: number[] }>;
}

export interface EdaKpiTrend {
  change_pct: number | null;
  previous_value: number;
  sparkline: number[];
}

export interface EdaKpi {
  key: string;
  label: string;
  value: number;
  format: "number" | "percent";
  source_column?: string;
  agg?: "mean" | "sum" | "rate";
  rate_value?: string;
  trend?: EdaKpiTrend;
}

export interface EdaCategoryItem {
  value: string;
  count: number;
  pct: number;
}

export interface EdaCategoryChart {
  column: string;
  label: string;
  items: EdaCategoryItem[];
}

export interface EdaRatingBreakdown {
  column: string;
  label: string;
  average: number;
  min: number;
  max: number;
  breakdown: { value: number; count: number; pct: number }[];
}

export interface EdaTimeTrend {
  date_column: string;
  labels: string[];
  series: { name: string; values: number[] }[];
}

export interface EdaFilterOptions {
  categorical: Record<string, string[]>;
  date_ranges: Record<string, { min: string; max: string }>;
}

export interface FilteredEdaResult {
  summary: {
    total_rows: number;
    filtered_rows: number;
    columns: number;
    is_filtered: boolean;
  };
  kpis: EdaKpi[];
  donut_charts: EdaCategoryChart[];
  bar_charts: EdaCategoryChart[];
  rating_breakdown: EdaRatingBreakdown | null;
  time_trend: EdaTimeTrend | null;
  filter_options: EdaFilterOptions;
}

export interface EdaDateRangeFilter {
  start?: string;
  end?: string;
}

export interface EdaFilterRequest {
  categorical_filters: Record<string, string[]>;
  date_filters: Record<string, EdaDateRangeFilter>;
}

export interface FeatureImportance {
  name: string;
  importance: number;
}

export interface BaselineComparison {
  baseline_score: number;
  baseline_metric: string;
  model_score: number;
  improvement: number;
  cv_std: number;
  clearly_beats_baseline: boolean;
}

export interface LeakageWarning {
  column: string;
  correlation: number;
  reason: string;
}

export interface PostOutcomeWarning {
  column: string;
  reason: string;
}

export interface ModelMetrics {
  [key: string]:
    | number
    | number[]
    | null
    | undefined
    | boolean
    | string
    | { labels: string[]; matrix: number[][] }
    | Record<string, number>
    | { actual: number; predicted: number }[]
    | BaselineComparison
    | LeakageWarning[]
    | PostOutcomeWarning[];
  cv_scores?: number[];
  cv_mean?: number | null;
  confusion_matrix?: { labels: string[]; matrix: number[][] };
  class_distribution?: Record<string, number>;
  actual_vs_predicted?: { actual: number; predicted: number }[];
  residuals_sample?: number[];
  baseline?: BaselineComparison;
  leakage_warnings?: LeakageWarning[];
  post_outcome_excluded?: PostOutcomeWarning[];
  post_outcome_included?: PostOutcomeWarning[];
  high_correlation_warnings?: LeakageWarning[];
  is_weak?: boolean;
  weak_reason?: string | null;
}

export interface MLModel {
  id: string;
  project_id: string;
  dataset_id: string;
  version: number;
  model_type: string;
  problem_type: "classification" | "regression";
  target_column: string;
  feature_columns_json: string[] | null;
  metrics_json: ModelMetrics | null;
  feature_importance_json: FeatureImportance[] | null;
  is_best: boolean;
  status: string;
  created_at: string;
}

export interface FeatureSchemaField {
  name: string;
  type: "numeric" | "categorical";
  default: number | string;
  min?: number | null;
  max?: number | null;
  options?: string[];
}

export interface ModelPreprocessing {
  numeric_features: string[];
  categorical_features: string[];
  test_size: number;
  cv_folds: number;
  excluded_id_like?: string[];
  excluded_datetime_raw?: string[];
  derived_date_features?: string[];
}

export interface ModelDetail extends MLModel {
  preprocessing_json: ModelPreprocessing | null;
  hyperparameters_json: Record<string, unknown> | null;
  comparison_json: { models: { model_type: string; label: string; metrics: ModelMetrics }[] } | null;
  feature_schema_json: FeatureSchemaField[] | null;
  random_seed: number;
}

export interface PredictionContributions {
  type: "instance_contribution" | "global_importance";
  note: string;
  items: ({ name: string; contribution: number } | FeatureImportance)[];
}

export interface Prediction {
  id: string;
  model_id: string;
  input_json: Record<string, unknown>;
  output_json: {
    predicted_class?: string;
    predicted_value?: number;
    probabilities?: Record<string, number>;
    confidence?: number;
    contributions?: PredictionContributions;
  };
  created_at: string;
}

export interface BatchPredictionResult {
  model_id: string;
  row_count: number;
  results: Record<string, unknown>[];
}

export interface ReturnPredictionCustomer {
  customer_id: string;
  predicted_return: boolean;
  return_probability: number;
  risk_group: string;
}

export interface ReturnPredictionsResult {
  available: boolean;
  reason?: string | null;
  window_days?: number | null;
  total_customers?: number | null;
  predicted_will_return?: number | null;
  predicted_will_not_return?: number | null;
  predicted_uncertain?: number | null;
  risk_bands?: Record<string, string> | null;
  risk_group_counts?: Record<string, number> | null;
  holdout_metrics?: Record<string, number> | null;
  customers: ReturnPredictionCustomer[];
}

export type AIStatusReason =
  | "ok"
  | "unreachable"
  | "timeout"
  | "server_error"
  | "model_not_found"
  | "not_configured"
  | "invalid_key"
  | "rate_limited"
  | "blocked"
  | "empty_response"
  | "error";

export interface AIStatus {
  available: boolean;
  model: string;
  provider?: "ollama" | "gemini";
  url?: string;
  reason?: AIStatusReason;
  detail?: string;
  installed_models?: string[];
  /** Present only when Gemini is the primary provider — Ollama's status as the fallback. */
  fallback?: AIStatus;
  /** True when this status came from the short-lived server-side cache (background
   * polling) rather than a fresh live request (an explicit "Test Connection" click). */
  cached?: boolean;
}

export interface InsightResponse {
  insight: string;
  ai_available: boolean;
  provider?: string;
  source_context: Record<string, unknown>;
  generated_at: string;
  /** True when this insight was loaded from the AI Insight cache (a prior successful
   * generation for this exact dataset/model version) rather than freshly generated. */
  cached: boolean;
}

export interface AIMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  ai_generated: boolean;
  created_at: string;
}

export interface AIConversation {
  id: string;
  project_id: string;
  title: string;
  created_at: string;
  messages: AIMessage[];
}

export interface Report {
  id: string;
  project_id: string;
  title: string;
  content_json: { ai_insight?: string | null; ai_available?: boolean; ai_provider?: string | null; generated_at?: string } | null;
  created_at: string;
}

export interface ApiError {
  detail: string;
}

export type StepStatus = "pending" | "running" | "completed" | "warning" | "skipped" | "failed";

export interface AutoAnalyzeStep {
  key: string;
  label: string;
  status: StepStatus;
  detail: string | null;
}

export interface AutoAnalyzeJob {
  id: string;
  project_id: string;
  dataset_id: string;
  status: "pending" | "running" | "awaiting_target_confirmation" | "completed" | "failed";
  current_step: string | null;
  steps_json: AutoAnalyzeStep[];
  target_candidates_json: TargetCandidate[];
  confirmed_target_column: string | null;
  result_json: {
    row_count?: number;
    column_count?: number;
    target_column?: string;
    best_model_id?: string;
    best_model_type?: string;
    problem_type?: string;
    best_model_score?: number;
    baseline?: BaselineComparison;
    is_weak_model?: boolean;
    weak_model_reason?: string | null;
    leakage_warnings?: LeakageWarning[];
    post_outcome_excluded?: PostOutcomeWarning[];
    excluded_id_like?: string[];
    excluded_datetime_raw?: string[];
    derived_date_features?: string[];
    ai_insight?: string | null;
    ai_available?: boolean;
    ai_provider?: string | null;
    ai_reason?: string | null;
    report_id?: string;
    analysis_type?: "supervised" | "clustering" | "general";
    clusters?: {
      method: string;
      k: number;
      silhouette_score: number;
      features_used: string[];
      rows_clustered: number;
      clusters: {
        cluster: number;
        size: number;
        pct: number;
        feature_means: Record<string, number>;
      }[];
    } | null;
  } | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}
