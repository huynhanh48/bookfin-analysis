import { useEffect, useMemo, useState } from "react";

import { fetchIndicators, fetchSeries, fetchStructure, fetchSymbols } from "./api";
import Chart, { type OverlayOptions } from "./components/Chart";
import AnalysisPanel from "./components/AnalysisPanel";
import RagPanel from "./components/RagPanel";
import {
  formatCompact,
  formatDate,
  formatDateTime,
  formatPercent,
  formatPrice,
  signalTone,
} from "./format";
import type {
  IndicatorSetResponse,
  IndicatorSeriesResponse,
  StructureSnapshot,
  SymbolOut,
  Timeframe,
} from "./types";

const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: "1D", label: "Ngày" },
  { value: "1W", label: "Tuần" },
];

const LIMITS = [120, 250, 500, 1000];

/** Chỉ báo FE cần cho biểu đồ; gửi rõ để có thêm chuỗi phân kỳ. */
const CHART_INDICATORS = [
  "sma:20",
  "sma:50",
  "ema:20",
  "bollinger:20:2",
  "volume_sma:20",
];

const formatParameters = (parameters: Record<string, number | string>): string => {
  if (String(parameters.algorithm_version).startsWith("swing-div-")) {
    return `Pivot ${parameters.pivot_length}/${parameters.pivot_right} · MACD ${parameters.use_histogram === 1 ? "Histogram" : "Line"} · RSI ${parameters.rsi_period}`;
  }
  const values = Object.values(parameters);
  if (values.length === 0) return "mặc định";
  return values.join(" / ");
};

const formatValue = (value: number | null): string => {
  if (value === null) return "—";
  return formatPrice(value);
};

const structureEventLabel: Record<string, string> = {
  BOS_BULLISH: "BOS tăng",
  BOS_BEARISH: "BOS giảm",
  CHOCH_BULLISH: "CHoCH tăng",
  CHOCH_BEARISH: "CHoCH giảm",
  MSS_BULLISH: "MSS tăng",
  MSS_BEARISH: "MSS giảm",
  FAILED_BREAKOUT_BULLISH: "Phá vỡ thất bại (tăng)",
  FAILED_BREAKOUT_BEARISH: "Phá vỡ thất bại (giảm)",
};

const biasTone = (bias: string | null | undefined): "up" | "down" | "flat" => {
  if (bias === "BULLISH") return "up";
  if (bias === "BEARISH") return "down";
  return "flat";
};

const trendTone = (trend: string | null | undefined): "up" | "down" | "flat" => {
  if (trend === "UPTREND") return "up";
  if (trend === "DOWNTREND") return "down";
  return "flat";
};

export default function App() {
  const [symbol, setSymbol] = useState("FPT");
  const [symbolInput, setSymbolInput] = useState("FPT");
  const [timeframe, setTimeframe] = useState<Timeframe>("1D");
  const [limit, setLimit] = useState(250);
  const [reloadToken, setReloadToken] = useState(0);
  const [divergenceConfig, setDivergenceConfig] = useState({
    rsi: 14, left: 5, maxGap: 150, right: 5, tolerance: 3, minGap: 5,
    lookback: 500, fast: 12, slow: 26, signal: 9, histogram: 0,
    priceDelta: 0, macdDelta: 0, rsiDelta: 0,
  });
  const [divergenceDraft, setDivergenceDraft] = useState(divergenceConfig);
  const [divergenceConfigError, setDivergenceConfigError] = useState("");
  const chartIndicators = useMemo(() => {
    const c = divergenceConfig;
    return [...CHART_INDICATORS, `rsi:${c.rsi}`, `macd:${c.fast}:${c.slow}:${c.signal}`,
      `divergence:${[c.rsi,c.left,c.maxGap,c.right,c.tolerance,c.minGap,c.lookback,c.fast,c.slow,c.signal,c.histogram,1,1,1,1,c.priceDelta,c.macdDelta,c.rsiDelta].join(":")}`];
  }, [divergenceConfig]);

  const [symbols, setSymbols] = useState<SymbolOut[]>([]);
  const [data, setData] = useState<IndicatorSeriesResponse | null>(null);
  const [indicatorSet, setIndicatorSet] = useState<IndicatorSetResponse | null>(null);
  const [structure, setStructure] = useState<StructureSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overlays, setOverlays] = useState<OverlayOptions>({
    sma: true,
    ema: true,
    bollinger: true,
    divergence: true,
  });

  useEffect(() => {
    let cancelled = false;
    fetchSymbols(600)
      .then((response) => {
        if (!cancelled) setSymbols(response.items);
      })
      .catch(() => {
        if (!cancelled) setSymbols([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.allSettled([
      fetchSeries(symbol, timeframe, limit, chartIndicators),
      fetchIndicators(symbol, timeframe),
      fetchStructure(symbol, timeframe),
    ]).then(([seriesResult, indicatorsResult, structureResult]) => {
      if (cancelled) return;

      if (seriesResult.status === "fulfilled") {
        setData(seriesResult.value);
      } else {
        setData(null);
        setError(
          seriesResult.reason instanceof Error
            ? seriesResult.reason.message
            : "Không tải được dữ liệu biểu đồ.",
        );
      }

      setIndicatorSet(indicatorsResult.status === "fulfilled" ? indicatorsResult.value : null);
      setStructure(structureResult.status === "fulfilled" ? structureResult.value : null);
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe, limit, reloadToken, chartIndicators]);

  const lastCandle = data?.candles.at(-1) ?? null;
  const previousCandle = data?.candles.at(-2) ?? null;
  const changePercent =
    lastCandle && previousCandle && previousCandle.close !== 0
      ? ((lastCandle.close - previousCandle.close) / previousCandle.close) * 100
      : null;

  const results = useMemo(
    () => (indicatorSet?.results ?? []).filter((result) => result.status === "ok"),
    [indicatorSet],
  );

  const latestEvents = useMemo(() => {
    if (!structure) return [];
    return [...structure.events].slice(-6).reverse();
  }, [structure]);

  const submitSymbol = () => {
    const next = symbolInput.trim().toUpperCase();
    if (next) setSymbol(next);
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">BF</div>
          <div>
            <h1>BookFin Chart</h1>
            <p>Nến, chỉ báo và tín hiệu từ backend fcdata</p>
          </div>
        </div>

        <form
          className="controls"
          onSubmit={(event) => {
            event.preventDefault();
            submitSymbol();
          }}
        >
          <label className="field">
            <span>Mã</span>
            <input
              value={symbolInput}
              onChange={(event) => setSymbolInput(event.target.value.toUpperCase())}
              list="symbol-options"
              placeholder="FPT"
              autoComplete="off"
            />
          </label>
          <datalist id="symbol-options">
            {symbols.map((item) => (
              <option key={item.symbol} value={item.symbol}>
                {item.company_name ?? item.symbol}
              </option>
            ))}
          </datalist>

          <label className="field">
            <span>Khung</span>
            <select
              value={timeframe}
              onChange={(event) => setTimeframe(event.target.value as Timeframe)}
            >
              {TIMEFRAMES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Số nến</span>
            <select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
              {LIMITS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>

          <button type="submit" className="primary">
            Xem
          </button>
          <button type="button" className="ghost" onClick={() => setReloadToken((value) => value + 1)}>
            Tải lại
          </button>
        </form>
      </header>

      <div className="statusbar">
        <div className="status-left">
          <span className="symbol">{symbol}</span>
          {lastCandle && (
            <>
              <span className={`price ${changePercent !== null && changePercent < 0 ? "down" : "up"}`}>
                {formatPrice(lastCandle.close)}
              </span>
              <span className={`badge ${changePercent !== null && changePercent < 0 ? "down" : "up"}`}>
                {formatPercent(changePercent)}
              </span>
            </>
          )}
          {data && (
            <span className="muted">
              {data.count} nến · cập nhật {formatDateTime(data.as_of)}
            </span>
          )}
        </div>
        <div className="status-right">
          {indicatorSet?.computed_on_demand && <span className="badge flat">tính tại chỗ</span>}
          {loading && <span className="muted">Đang tải…</span>}
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <details open className="rag-entry"><summary>Phân tích cổ phiếu — Evidence RAG</summary><AnalysisPanel /></details>
      <details className="rag-entry"><summary>Nạp dữ liệu / thử RAG</summary><RagPanel /></details>
      <main className="layout">
        <section className="card chart-card">
          <div className="card-head">
            <div>
              <h2>
                {symbol} · {timeframe === "1D" ? "Ngày" : "Tuần"}
              </h2>
              <p className="muted">Biểu đồ nến với đường chỉ báo và mốc tín hiệu.</p>
            </div>
            <div className="toggles">
              <label>
                <input
                  type="checkbox"
                  checked={overlays.sma}
                  onChange={(event) =>
                    setOverlays((current) => ({ ...current, sma: event.target.checked }))
                  }
                />
                SMA
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={overlays.ema}
                  onChange={(event) =>
                    setOverlays((current) => ({ ...current, ema: event.target.checked }))
                  }
                />
                EMA
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={overlays.bollinger}
                  onChange={(event) =>
                    setOverlays((current) => ({ ...current, bollinger: event.target.checked }))
                  }
                />
                Bollinger
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={overlays.divergence}
                  onChange={(event) =>
                    setOverlays((current) => ({ ...current, divergence: event.target.checked }))
                  }
                />
                Phân kỳ
              </label>
              {overlays.divergence && ([
                ["divergenceMacd", "MACD"], ["divergenceRsi", "RSI"],
                ["divergenceBullish", "Bull"], ["divergenceBearish", "Bear"],
                ["divergenceLines", "Đường nối"], ["divergenceLabels", "Nhãn xác nhận"],
              ] as const).map(([key, label]) => (
                <label key={key}><input type="checkbox" checked={overlays[key] !== false}
                  onChange={event => setOverlays(current => ({...current, [key]: event.target.checked}))} />{label}</label>
              ))}
            </div>
          </div>

          <details className="divergence-settings">
            <summary>Cấu hình phân kỳ swing MACD / RSI</summary>
            <form onSubmit={event => {
              event.preventDefault();
              const c = divergenceDraft;
              if (c.minGap > c.maxGap || c.fast >= c.slow) {
                setDivergenceConfigError("Cần Min gap ≤ Max gap và MACD Fast < Slow."); return;
              }
              setDivergenceConfigError(""); setDivergenceConfig({...c});
            }}>
              <div style={{display: "flex", flexWrap: "wrap", gap: 12, padding: "8px 0"}}>
                {([
                  ["left", "Pivot Left", 1, 50, 1], ["right", "Pivot Right", 1, 50, 1],
                  ["tolerance", "Lệch ±nến", 0, 20, 1], ["minGap", "Min gap", 1, 2000, 1],
                  ["maxGap", "Max gap", 1, 2000, 1], ["lookback", "Lookback", 10, 2000, 1],
                  ["rsi", "RSI length", 2, 200, 1], ["fast", "MACD Fast", 2, 200, 1],
                  ["slow", "MACD Slow", 3, 400, 1], ["signal", "MACD Signal", 2, 200, 1],
                  ["priceDelta", "Chênh giá tối thiểu %", 0, 1000000, 0.1],
                  ["macdDelta", "Chênh MACD tối thiểu", 0, 1000000000, 0.001],
                  ["rsiDelta", "Chênh RSI tối thiểu", 0, 100, 0.1],
                ] as const).map(([key, label, min, max, step]) => (
                  <label key={key}>{label}<input type="number" required min={min} max={max} step={step}
                    style={{display: "block", width: 95}} value={divergenceDraft[key]}
                    onChange={event => setDivergenceDraft(c => ({...c, [key]: Number(event.target.value)}))} /></label>
                ))}
                <label>Nguồn MACD<select value={divergenceDraft.histogram}
                  onChange={event => setDivergenceDraft(c => ({...c, histogram: Number(event.target.value)}))}>
                  <option value={0}>MACD Line</option><option value={1}>Histogram</option>
                </select></label>
                <button className="primary" type="submit">Áp dụng</button>
              </div>
              {divergenceConfigError && <p role="alert">{divergenceConfigError}</p>}
              <p className="muted small">Chỉ xác nhận sau Pivot Right + tolerance nến đã đóng. Đường nối là vị trí swing lịch sử; nhãn nằm ở nến xác nhận.</p>
            </form>
          </details>

          <div className="chart-body">
            {data ? (
              <Chart data={data} structure={structure} overlays={overlays} />
            ) : (
              <div className="chart-placeholder">
                {loading ? "Đang tải biểu đồ…" : "Chưa có dữ liệu để vẽ."}
              </div>
            )}
          </div>
        </section>

        <aside className="sidebar">
          <section className="card">
            <div className="card-head">
              <h2>Tín hiệu chỉ báo</h2>
              <span className="muted">{results.length} chỉ báo</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Chỉ báo</th>
                    <th>Giá trị</th>
                    <th>Tín hiệu</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((result) => (
                    <tr key={`${result.indicator}-${formatParameters(result.parameters)}`}>
                      <td>
                        <div className="indicator-name">{result.indicator.toUpperCase()}</div>
                        <div className="muted small">{formatParameters(result.parameters)}</div>
                      </td>
                      <td>
                        <div className="value-list">
                          {Object.entries(result.values).filter(([key]) => result.indicator !== "divergence" || !key.includes("hidden")).map(([key, value]) => (
                            <span key={key}>
                              <em>{key}</em> {formatValue(value)}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td>
                        {result.signal ? (
                          <span className={`badge ${signalTone(result.signal)}`}>{result.signal}</span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {results.length === 0 && (
                    <tr>
                      <td colSpan={3} className="muted center">
                        {loading ? "Đang tải…" : "Không có chỉ báo."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>Cấu trúc thị trường</h2>
              {structure && (
                <span className={`badge ${biasTone(structure.break_bias)}`}>{structure.break_bias}</span>
              )}
            </div>
            {structure ? (
              <div className="structure">
                <div className="structure-row">
                  <span className="muted">Xu hướng xác nhận</span>
                  <span className={`badge ${trendTone(structure.confirmed_trend)}`}>
                    {structure.confirmed_trend}
                  </span>
                </div>
                <div className="structure-grid">
                  <div>
                    <span className="muted">Swing high</span>
                    <strong>{structure.last_swing_high ? formatPrice(structure.last_swing_high.price) : "—"}</strong>
                    <small>{structure.last_swing_high ? formatDate(structure.last_swing_high.pivot_time) : ""}</small>
                  </div>
                  <div>
                    <span className="muted">Swing low</span>
                    <strong>{structure.last_swing_low ? formatPrice(structure.last_swing_low.price) : "—"}</strong>
                    <small>{structure.last_swing_low ? formatDate(structure.last_swing_low.pivot_time) : ""}</small>
                  </div>
                  <div>
                    <span className="muted">Protected high</span>
                    <strong>{structure.protected_high ? formatPrice(structure.protected_high.price) : "—"}</strong>
                  </div>
                  <div>
                    <span className="muted">Protected low</span>
                    <strong>{structure.protected_low ? formatPrice(structure.protected_low.price) : "—"}</strong>
                  </div>
                </div>

                <div className="events">
                  <div className="events-head">
                    <span className="muted">Sự kiện gần nhất</span>
                    <span className="muted small">{structure.events.length} sự kiện</span>
                  </div>
                  {latestEvents.length === 0 && <p className="muted small">Chưa có sự kiện.</p>}
                  {latestEvents.map((event) => (
                    <div className="event-row" key={`${event.event_type}-${event.broken_at}`}>
                      <span className={`badge ${biasTone(event.bias_after)}`}>
                        {structureEventLabel[event.event_type] ?? event.event_type}
                      </span>
                      <span>{formatPrice(event.price)}</span>
                      <span className="muted small">{formatDate(event.broken_at)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="muted small">{loading ? "Đang tải…" : "Không có dữ liệu cấu trúc."}</p>
            )}
          </section>

          <section className="card">
            <div className="card-head">
              <h2>Chú giải</h2>
            </div>
            <ul className="legend-list">
              <li>
                <span className="dot up" /> Nến tăng / tín hiệu tăng
              </li>
              <li>
                <span className="dot down" /> Nến giảm / tín hiệu giảm
              </li>
              <li>
                <span className="marker-text">MACD↑</span> MACD cắt lên đường tín hiệu
              </li>
              <li>
                <span className="marker-text">MACD↓</span> MACD cắt xuống đường tín hiệu
              </li>
              <li>
                <span className="marker-text">BOS↑ / BOS↓</span> Phá vỡ cấu trúc
              </li>
              <li>
                <span className="marker-text">CHoCH↑ / CHoCH↓</span> Đổi chiều xu hướng
              </li>
            </ul>
            <p className="muted small">
              KL: {formatCompact(lastCandle?.volume)} · Nguồn dữ liệu: fcdata qua backend.
            </p>
          </section>
        </aside>
      </main>
    </div>
  );
}
