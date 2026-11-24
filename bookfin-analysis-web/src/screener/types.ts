export interface ToggleCondition {
  enabled: boolean;
}

export interface MinVolumeCondition extends ToggleCondition {
  value: number;
}

export interface MinPriceCondition extends ToggleCondition {
  value: number;
}

export interface RelativeVolumeCondition extends ToggleCondition {
  value: number;
  lookback: number;
}

export interface PriceChangeCondition extends ToggleCondition {
  percent: number;
}

export interface PriceVsMA5Condition extends ToggleCondition {
  percent: number;
  period: number;
}

export interface DivergenceCondition extends ToggleCondition {
  direction: "bullish" | "bearish" | "any";
  kind: "regular" | "hidden" | "any";
  window: "latest" | "range";
  days: number;
  basis: "pivot" | "confirmed";
  date_from: string | null;
  date_to: string | null;
}

export interface ScreenerConditions {
  min_volume: MinVolumeCondition;
  min_price: MinPriceCondition;
  relative_volume: RelativeVolumeCondition;
  price_change: PriceChangeCondition;
  price_vs_ma5: PriceVsMA5Condition;
  divergence: DivergenceCondition;
}

export type ScreenerStatus = "LIVE" | "CONFIRMED" | "STALE";
export type ScreenerMode = "historical" | "realtime";
export type ScreenerFeedStatus = "LIVE" | "STALE" | "OFFLINE";

export interface ScreenerRow {
  symbol: string;
  price: number;
  change_percent: number;
  volume: number;
  volume_sma: number;
  relative_volume: number;
  ma5: number;
  price_vs_ma5_percent: number;
  status: ScreenerStatus;
  updated_at: string;
  matched_conditions: string[];
  divergence?: string | null;
}

export interface ScreenerScanResponse {
  profile_id: string;
  mode: ScreenerMode;
  as_of: string;
  generated_at: string;
  feed_status: ScreenerFeedStatus;
  total_universe: number;
  evaluated: number;
  matched: number;
  skipped: number;
  results: ScreenerRow[];
}

export interface ScreenerScanRequest {
  mode: ScreenerMode;
  universe: "all" | "symbols";
  symbols?: string[] | null;
  conditions: ScreenerConditions;
  limit?: number;
}

export interface WatchlistOut {
  id: number;
  owner_id: string;
  name: string;
  sort_order: number;
  is_default: boolean;
  item_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface WatchlistListResponse {
  items: WatchlistOut[];
  total: number;
}

export interface WatchlistItemOut {
  symbol: string;
  sort_order: number;
  created_at?: string | null;
}

export interface WatchlistItemsResponse {
  watchlist_id: number;
  name: string;
  items: WatchlistItemOut[];
  count: number;
}

export interface WatchlistBulkAddResult {
  watchlist_id: number;
  added: number;
  skipped: number;
  symbols: string[];
}
