const MARKET_TZ = "Asia/Ho_Chi_Minh";

const dateTimeFormatter = new Intl.DateTimeFormat("vi-VN", {
  timeZone: MARKET_TZ,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const timeFormatter = new Intl.DateTimeFormat("vi-VN", {
  timeZone: MARKET_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return dateTimeFormatter.format(new Date(iso));
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return `${dateTimeFormatter.format(date)} ${timeFormatter.format(date)}`;
}

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("vi-VN", { maximumFractionDigits: 2 });
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} tỷ`;
  }
  if (Math.abs(value) >= 1_000_000) {
    return `${(value / 1_000_000).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} tr`;
  }
  return value.toLocaleString("vi-VN", { maximumFractionDigits: 0 });
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

export function signalTone(signal: string | null | undefined): "up" | "down" | "flat" {
  if (!signal) return "flat";
  const value = signal.toLowerCase();
  if (
    value.includes("bull") ||
    value.includes("oversold") ||
    value.includes("above") ||
    value.includes("uptrend") ||
    value.includes("outperform")
  ) {
    return "up";
  }
  if (
    value.includes("bear") ||
    value.includes("overbought") ||
    value.includes("below") ||
    value.includes("downtrend") ||
    value.includes("weak") ||
    value.includes("underperform")
  ) {
    return "down";
  }
  return "flat";
}
