import type { ScreenerRow } from "./types";

export interface ScreenerUpdate {
  profileId: string;
  rows: ScreenerRow[];
  feedStatus: string;
  generatedAt: string;
  scanSeedAt: string | null;
}

interface WireEvent {
  type?: string;
  channel?: string;
  data?: Record<string, unknown>;
}

/** Một WebSocket cho toàn bộ screener; channel theo profile_id, không theo symbol. */
export class ScreenerSocket {
  private socket: WebSocket | null = null;
  private retry: ReturnType<typeof setTimeout> | undefined;
  private heartbeat: ReturnType<typeof setInterval> | undefined;
  private generation = 0;
  private profileId: string | null = null;

  constructor(
    private readonly onUpdate: (update: ScreenerUpdate) => void,
    private readonly onStatus: (message: string, level: "ok" | "error") => void,
  ) {}

  connect(profileId: string): void {
    this.close();
    this.profileId = profileId;
    const generation = this.generation;
    let attempts = 0;

    const connect = () => {
      if (generation !== this.generation || this.profileId !== profileId) return;
      const url = new URL("/api/v1/ws", window.location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(url);
      this.socket = socket;
      socket.onmessage = (event) => {
        if (generation !== this.generation) return;
        try {
          const message = JSON.parse(String(event.data)) as WireEvent;
          if (message.type === "ready") {
            attempts = 0;
            socket.send(
              JSON.stringify({ type: "subscribe", channel: `screener:${profileId}` }),
            );
            this.heartbeat = setInterval(() => {
              if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ type: "ping" }));
              }
            }, 20000);
            this.onStatus("Realtime screener đã kết nối.", "ok");
            return;
          }
          if (message.type === "error") {
            this.onStatus("Không subscribe được kênh screener.", "error");
            return;
          }
          if (message.type !== "screener.update" || !message.data) return;
          const rows = Array.isArray(message.data.rows)
            ? (message.data.rows as ScreenerRow[])
            : [];
          this.onUpdate({
            profileId,
            rows,
            feedStatus: String(message.data.feed_status ?? "LIVE"),
            generatedAt: String(message.data.generated_at ?? new Date().toISOString()),
            scanSeedAt: typeof message.data.scan_seed_at === "string" ? message.data.scan_seed_at : null,
          });
        } catch {
          this.onStatus("Không đọc được bản tin screener.", "error");
        }
      };
      socket.onclose = (event) => {
        if (generation !== this.generation) return;
        clearInterval(this.heartbeat);
        if (event.code === 1008) {
          this.onStatus("WebSocket screener bị từ chối quyền truy cập.", "error");
          return;
        }
        this.onStatus("Realtime screener mất kết nối; đang thử lại.", "error");
        this.retry = setTimeout(
          connect,
          Math.min(30000, 1000 * 2 ** attempts++) + Math.random() * 500,
        );
      };
      socket.onerror = () => socket.close();
    };
    connect();
  }

  close(): void {
    this.generation++;
    this.profileId = null;
    clearTimeout(this.retry);
    clearInterval(this.heartbeat);
    this.socket?.close();
    this.socket = null;
  }
}
