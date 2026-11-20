/** Persist Pro UI settings, including changes made by its internal toolbar. */
import type { KLineChartPro } from "@klinecharts/pro";

const KEY = "bookfin:workspace-settings:v1";
type WorkspaceSettings = {
  theme: ReturnType<KLineChartPro["getTheme"]>;
  locale: ReturnType<KLineChartPro["getLocale"]>;
  timezone: ReturnType<KLineChartPro["getTimezone"]>;
  styles: ReturnType<KLineChartPro["getStyles"]>;
  symbol: ReturnType<KLineChartPro["getSymbol"]>;
  period: ReturnType<KLineChartPro["getPeriod"]>;
};

export function readWorkspaceSettings(): Partial<WorkspaceSettings> {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "null");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    const result: Partial<WorkspaceSettings> = {};
    if (["light", "dark"].includes(value.theme)) result.theme = value.theme;
    if (["en-US", "zh-CN"].includes(value.locale)) result.locale = value.locale;
    if (typeof value.timezone === "string") {
      try { new Intl.DateTimeFormat("en", { timeZone: value.timezone }); result.timezone = value.timezone; } catch { /* Invalid saved timezone. */ }
    }
    if (value.styles && typeof value.styles === "object" && !Array.isArray(value.styles)) result.styles = value.styles;
    if (typeof value.symbol?.ticker === "string" && /^[A-Z0-9._-]{1,20}$/.test(value.symbol.ticker)) result.symbol = value.symbol;
    if (value.period?.multiplier === 1 && ["day", "week"].includes(value.period.timespan)) result.period = value.period;
    return result;
  } catch { return {}; }
}

/** Poll getters because Pro's internal Solid toolbar bypasses public setter wrappers. */
export function attachWorkspacePersistence(chart: KLineChartPro): () => void {
  let previous = "";
  const save = () => {
    try {
      const value: WorkspaceSettings = { theme: chart.getTheme(), locale: chart.getLocale(), timezone: chart.getTimezone(), styles: chart.getStyles(), symbol: chart.getSymbol(), period: chart.getPeriod() };
      const serialized = JSON.stringify(value);
      if (serialized !== previous) { localStorage.setItem(KEY, serialized); previous = serialized; }
    } catch { /* Storage unavailable: keep the chart functional. */ }
  };
  const timer = window.setInterval(save, 500);
  const visibility = () => { if (document.hidden) save(); };
  window.addEventListener("pagehide", save);
  window.addEventListener("beforeunload", save);
  document.addEventListener("visibilitychange", visibility);
  return () => {
    save(); window.clearInterval(timer);
    window.removeEventListener("pagehide", save);
    window.removeEventListener("beforeunload", save);
    document.removeEventListener("visibilitychange", visibility);
  };
}
