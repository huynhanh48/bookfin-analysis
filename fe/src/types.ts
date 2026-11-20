/** Kiểu dữ liệu phản chiếu schema backend. */

export type Timeframe = "1D" | "1W";

export interface SymbolOut {
  symbol: string;
  exchange: string | null;
  company_name: string | null;
  sector: string | null;
  active: boolean;
}

export interface SymbolListResponse {
  items: SymbolOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface CandleOut {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  value: number | null;
  price_basis: string;
  source: string;
}

export interface IndicatorPoint {
  time: string;
  values: Record<string, number | null>;
  signal: string | null;
}

export interface IndicatorSeries {
  key: string;
  indicator: string;
  parameters: Record<string, number | string>;
  points: IndicatorPoint[];
  events?: DivergenceEvent[];
}

export interface IndicatorSeriesResponse {
  symbol: string;
  timeframe: string;
  as_of: string | null;
  count: number;
  candles: CandleOut[];
  series: IndicatorSeries[];
}

export interface IndicatorResult {
  symbol: string;
  timeframe: string;
  as_of: string | null;
  indicator: string;
  parameters: Record<string, number | string>;
  values: Record<string, number | null>;
  signal: string | null;
  warmup: number;
  status: "ok" | "insufficient_history";
  is_final_candle: boolean;
  engine_version: string;
}

export interface IndicatorSetResponse {
  symbol: string;
  timeframe: string;
  as_of: string | null;
  candles_used: number;
  computed_on_demand: boolean;
  results: IndicatorResult[];
}

export type BreakBias = "BULLISH" | "BEARISH" | "NEUTRAL";
export type ConfirmedTrend = "UPTREND" | "DOWNTREND" | "SIDEWAYS" | "TRANSITION";

export interface SwingPoint {
  kind: "HIGH" | "LOW";
  label: "HH" | "HL" | "LH" | "LL" | "EQH" | "EQL" | null;
  price: number;
  pivot_time: string;
  confirmed_at: string;
  index: number;
}

export interface StructureEvent {
  event_type:
    | "BOS_BULLISH"
    | "BOS_BEARISH"
    | "CHOCH_BULLISH"
    | "CHOCH_BEARISH"
    | "MSS_BULLISH"
    | "MSS_BEARISH"
    | "FAILED_BREAKOUT_BULLISH"
    | "FAILED_BREAKOUT_BEARISH";
  pivot_time: string;
  confirmed_at: string;
  broken_at: string;
  price: number;
  break_level: number;
  observed_price: number;
  bias_before: BreakBias;
  bias_after: BreakBias;
  is_displacement: boolean;
}

export interface StructureSnapshot {
  symbol: string;
  timeframe: string;
  as_of: string | null;
  status: "ok" | "insufficient_history";
  break_bias: BreakBias;
  confirmed_trend: ConfirmedTrend;
  last_swing_high: SwingPoint | null;
  last_swing_low: SwingPoint | null;
  protected_high: SwingPoint | null;
  protected_low: SwingPoint | null;
  swings: SwingPoint[];
  events: StructureEvent[];
  latest_event: StructureEvent | null;
  candle_finality: "closed" | "forming";
}


export interface DivergenceEvent {
  id: string;
  kind: "regular";
  direction: "bullish" | "bearish";
  start_index: number;
  end_index: number;
  start_time: string;
  end_time: string;
  start_price: number;
  end_price: number;
  price_confirmed_at: string;
  confirmed_at: string;
  label: string;
  alerts: string[];
  oscillators: {
    indicator: "MACD" | "RSI";
    start_index: number;
    end_index: number;
    start_time: string;
    end_time: string;
    start_value: number;
    end_value: number;
  }[];
}

/* ------------------------------------------------------------------ */
/* Analysis Case Memory / Evidence RAG                                 */
/* ------------------------------------------------------------------ */

export type VerificationStatus =
  | "VERIFIED"
  | "CONTRADICTED"
  | "INSUFFICIENT_EVIDENCE"
  | "NOT_VERIFIABLE";

export type JobStage =
  | "PENDING"
  | "EXTRACTING"
  | "ENRICHING"
  | "VERIFYING"
  | "NORMALIZING"
  | "INDEXING"
  | "COMPLETED"
  | "PARTIAL"
  | "FAILED"
  | "NEEDS_REVIEW"
  | "PROCESSING";

export interface AnalysisJob {
  id: string;
  case_id: string | null;
  status: string;
  stage: JobStage | string;
  progress: number;
  attempts: number;
  error_code: string | null;
  diagnostics: Record<string, unknown>;
}

export interface ClaimTemporalScope {
  kind: "analysis" | "divergence" | "explicit" | "unknown";
  start?: string | null;
  end?: string | null;
  basis?: string;
}

export interface TechnicalClaim {
  claim_id?: string;
  claim_type: string;
  original_text: string;
  extracted_parameters?: Record<string, unknown>;
  temporal_scope?: ClaimTemporalScope | null;
  source_type: "user" | "vision" | string;
  verification_status: VerificationStatus | string;
  verification_reason: string;
  evidence_ids: string[];
}

export interface VisionObservations {
  symbol_candidates?: string[];
  timeframe?: string | null;
  chart_observed_at?: string | null;
  exchange?: string | null;
  description?: string;
  visible_indicators?: string[];
  price_structure?: string[];
  trendlines?: string[];
  support_resistance?: string[];
  candle_patterns?: string[];
  divergence_observations?: string[];
  volume_observations?: string[];
  annotations?: string[];
  uncertainties?: string[];
}

export interface AnalysisSource {
  evidence_id?: string;
  case_id?: string;
  source_url?: string;
  canonical_url?: string;
  title?: string;
  publisher?: string;
  published_at?: string | null;
  updated_at?: string | null;
  retrieved_at?: string | null;
  content_hash?: string;
  source_type?: string;
  analyzed_at?: string | null;
  as_of?: string | null;
  engine_version?: string | null;
  source?: string | null;
}

export interface TechnicalSnapshot {
  status?: string;
  reason?: string;
  symbol?: string;
  timeframe?: string;
  as_of?: string | null;
  bars?: number;
  price_basis?: string;
  rsi_at_analysis?: number | null;
  engine_version?: string;
  ta_lib_version?: string;
  filter_values?: Record<string, number | null>;
  divergence_events?: unknown[];
  structure_events?: unknown[];
  market_structure?: {
    confirmed_trend?: string;
    swings?: unknown[];
    events?: unknown[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface EmbeddingInfo {
  kind: string;
  model: string;
  dimension: number;
  content_hash?: string | null;
  image_hash?: string | null;
}

export interface AnalysisEnrichment {
  vision_observations?: VisionObservations;
  resolved_context?: Record<string, unknown>;
  technical_snapshot?: TechnicalSnapshot;
  normalized_text?: string;
  verification_summary?: Record<string, number>;
  claims?: TechnicalClaim[];
  sources?: AnalysisSource[];
  diagnostics?: Record<string, unknown>;
  embeddings?: EmbeddingInfo[];
}

export interface AnalysisCase {
  id: string;
  symbol: string | null;
  timeframe: string | null;
  analyzed_at: string | null;
  thesis: string;
  indicator_snapshot: Record<string, unknown>;
  market_context: Record<string, unknown>;
  conclusion: string;
  source: string;
  exchange?: string | null;
  price_basis?: string;
  company_name?: string;
  sector?: string;
  custom_metadata?: Record<string, unknown>;
  chart_path: string | null;
  ingest_status: string;
  enrichment?: AnalysisEnrichment;
  outcome?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface AnalysisCaseResponse {
  data: AnalysisCase;
  job_id?: string | null;
}

export interface AnalysisMatch {
  similarity: number;
  case: AnalysisCase;
  fusion_score?: number | null;
  channel_similarities?: Record<string, number>;
  rerank_score?: number | null;
}

export interface AnalysisSearchResponse {
  data: AnalysisMatch[];
  case_ids: string[];
  verification_summary: Record<string, number>;
  retrieval_metadata: Record<string, unknown>;
  rerank_metadata: Record<string, unknown>;
  sources: AnalysisSource[];
}

export interface EvidenceAnswer {
  answer: string;
  case_ids: string[];
  sources: AnalysisSource[];
  verification_summary: Record<string, unknown>;
  retrieval_metadata: Record<string, unknown>;
  rerank_metadata: Record<string, unknown>;
}

export type IndicatorConditionField =
  | "rsi"
  | "adx"
  | "atr"
  | "rvol"
  | "obv"
  | "macd"
  | "macd_histogram";

export interface IndicatorCondition {
  field: IndicatorConditionField;
  operator: "gte" | "lte" | "eq";
  value: number;
}
