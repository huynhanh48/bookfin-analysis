import type { KLineData } from "klinecharts";
import { getAdminKey } from "./apiAuth";
import { applyProvisionalIndicators, markConfirmed } from "./seriesStore";

/** One chart subscription; cancellation invalidates all old socket callbacks. */
export class ChartRealtime {
  private socket: WebSocket | null = null;
  private retry: ReturnType<typeof setTimeout> | undefined;
  private heartbeat: ReturnType<typeof setInterval> | undefined;
  private generation = 0;

  constructor(private readonly status?: (message: string, level: "ok" | "error") => void) {}

  subscribe(channel: string, callback: (bar: KLineData) => void, minimumTimestamp = 0): void {
    this.close();
    const generation = this.generation;
    let attempts = 0;
    let latest = minimumTimestamp;
    const connect = () => {
      if (generation !== this.generation) return;
      const url = new URL('/api/v1/ws', window.location.href);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(url);
      this.socket = socket;
      // The server waits for the first frame before sending `ready` whenever
      // ADMIN_API_KEY is configured. Browsers cannot set X-Admin-Key on a
      // WebSocket handshake, so authenticate as soon as the socket opens.
      socket.onopen = () => {
        const key = getAdminKey();
        if (key) socket.send(JSON.stringify({ type: "authenticate", token: key }));
      };
      socket.onmessage = event => {
        if (generation !== this.generation) return;
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'ready') {
            attempts = 0;
            socket.send(JSON.stringify({type:'subscribe', channel}));
            this.heartbeat = setInterval(() => {
              if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type:'ping'}));
            }, 20000);
          } else if (message.type === 'candle' && message.channel === channel) {
            const bar = message.data;
            // Chỉ báo tạm tính cho nến đang chạy, cùng engine TA-Lib với BE.
            if (bar.indicators && Number.isFinite(bar.timestamp)) {
              applyProvisionalIndicators(
                `${channel.split(':')[1]}:${channel.split(':')[2]}`,
                bar.timestamp,
                bar.indicators,
              );
            }
            if (![bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume].every(Number.isFinite)
              || bar.timestamp < latest || bar.volume < 0
              || bar.high < Math.max(bar.open, bar.close, bar.low)
              || bar.low > Math.min(bar.open, bar.close, bar.high)) return;
            latest = bar.timestamp;
            const storeKey = `${channel.split(':')[1]}:${channel.split(':')[2]}`;
            if (bar.is_final === true) markConfirmed(storeKey, bar.timestamp);
            callback({timestamp:bar.timestamp, open:bar.open, high:bar.high, low:bar.low, close:bar.close, volume:bar.volume});
            const stateLabel = bar.is_final === true ? 'nến đã xác nhận' : 'snapshot trong phiên (tạm thời)';
            this.status?.(`${channel.split(':')[1]} · ${channel.split(':')[2]} · ${stateLabel}`, 'ok');
          } else if (message.type === 'status' || message.type === 'error') {
            this.status?.('Realtime chưa có snapshot mới; giữ dữ liệu hiện tại.', 'error');
          }
        } catch {
          this.status?.('Không đọc được bản tin realtime.', 'error');
        }
      };
      socket.onclose = event => {
        if (generation !== this.generation) return;
        clearInterval(this.heartbeat);
        if (event.code === 1008) {
          this.status?.('WebSocket bị từ chối: kiểm tra quyền truy cập và origin của backend.', 'error');
          return;
        }
        this.status?.('Realtime mất kết nối; đang kết nối lại.', 'error');
        this.retry = setTimeout(connect, Math.min(30000, 1000 * 2 ** attempts++) + Math.random() * 500);
      };
      socket.onerror = () => socket.close();
    };
    connect();
  }

  close(): void {
    this.generation++;
    clearTimeout(this.retry);
    clearInterval(this.heartbeat);
    this.socket?.close();
    this.socket = null;
  }
}
