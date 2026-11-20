import { useEffect, useState } from "react";

type Job = {
  id: string;
  kind: string;
  source: string;
  status: string;
  error_code: string | null;
};

type RagSource = Record<string, unknown>;

type QueryResult = {
  answer: string;
  sources?: RagSource[];
  rerank_metadata?: Record<string, unknown>;
};

const MODES = [
  { value: "hybrid", label: "Hybrid (khuyến nghị)" },
  { value: "mix", label: "Mix" },
  { value: "local", label: "Local" },
  { value: "global", label: "Global" },
  { value: "naive", label: "Naive" },
];

const ACTIVE_STATUSES = new Set([
  "PENDING",
  "PROCESSING",
  "EXTRACTING",
  "ENRICHING",
  "VERIFYING",
  "NORMALIZING",
  "INDEXING",
]);

const samples = [
  {
    name: "Quy tắc xác minh nguồn",
    source: "TEST-RAG-001",
    text: "Tài liệu thử nghiệm TEST-RAG-001. Nhóm nghiên cứu Sao Mai yêu cầu mọi nhận định breakout phải kiểm tra đồng thời cấu trúc giá, khối lượng và thời điểm đóng nến. Mã xác minh riêng của tài liệu là SAOMAI-742. Đây là dữ liệu giả lập, không phải khuyến nghị đầu tư.",
    question: "Theo tài liệu TEST-RAG-001, breakout cần kiểm tra những gì? Mã xác minh là gì?",
  },
  {
    name: "Case minh họa",
    source: "TEST-RAG-002",
    text: "Case giả lập TEST-RAG-002: mã DEMO, khung D1. RSI 58, ADX 27, volume_ratio 1.8. Người phân tích ghi nhận HH/HL và giá vượt đỉnh trước. Cần chờ nến đóng để xác nhận. Chưa có dữ liệu kết quả sau phân tích. Không suy diễn lợi nhuận tương lai.",
    question: "Case DEMO có chỉ báo và cấu trúc nào? Đã biết kết quả sau phân tích chưa?",
  },
];

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function kindLabel(kind: string): string {
  if (kind === "image") return "Ảnh chart";
  if (kind === "document") return "PDF";
  return "Văn bản";
}

function sourceHref(source: RagSource): string | null {
  const value = source.source_url ?? source.file_path ?? source.url;
  return typeof value === "string" && value.length > 0 ? value : null;
}

function sourceLabel(source: RagSource, index: number): string {
  const value = source.title ?? source.file_path ?? source.source_url ?? source.id;
  return typeof value === "string" && value.length > 0 ? value : `Nguồn ${index + 1}`;
}

function sourceSnippet(source: RagSource): string | null {
  const value = source.content ?? source.text ?? source.snippet;
  if (typeof value !== "string" || !value.trim()) return null;
  return value.slice(0, 240);
}

export default function RagPanel() {
  const [kind, setKind] = useState("text");
  const [source, setSource] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [key, setKey] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<RagSource[]>([]);
  const [rerank, setRerank] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (key.trim()) headers.set("X-Admin-Key", key.trim());
    const response = await fetch("/api/v1/rag" + path, { ...init, headers });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const message =
        body && typeof body === "object" && "error" in body
          ? String((body as { error?: { message?: string } }).error?.message ?? "Yêu cầu thất bại")
          : `HTTP ${response.status}`;
      throw new Error(message);
    }
    return (body as { data: T }).data;
  }

  async function refresh(id: string) {
    try {
      const job = await request<Job>("/documents/" + id);
      setJobs((current) => current.map((item) => (item.id === id ? job : item)));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  useEffect(() => {
    const active = jobs.filter((job) => ACTIVE_STATUSES.has(job.status));
    if (active.length === 0) return;
    const timer = window.setInterval(() => {
      active.forEach((job) => {
        void refresh(job.id);
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [jobs, key]);

  async function action(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rag-panel" id="rag">
      <h2>Kho tri thức RAG — nạp dữ liệu và hỏi AI</h2>
      <p>
        Luồng này là <strong>Knowledge RAG</strong>: nạp text/chart/PDF vào kho tri thức
        LightRAG rồi hỏi. Case phân tích có vision/claim/technical/news dùng panel
        <strong> “Phân tích cổ phiếu — Evidence RAG”</strong> phía trên.
      </p>
      <label>
        Admin key (nếu backend yêu cầu)
        <input
          type="password"
          autoComplete="off"
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
      </label>

      <div className="rag-columns">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void action(async () => {
              if (kind !== "text" && !file) throw new Error("Hãy chọn file.");
              if (!source.trim()) throw new Error("Nhập tên nguồn.");
              if (kind === "text" && !text.trim()) throw new Error("Nhập nội dung văn bản.");
              if (file && file.size > 10 * 1024 * 1024) {
                throw new Error("File tối đa 10 MB.");
              }
              const form = new FormData();
              form.set("metadata", JSON.stringify({ kind, source: source.trim(), text }));
              if (kind !== "text" && file) form.set("file", file);
              const job = await request<Job>("/documents", { method: "POST", body: form });
              setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
            });
          }}
        >
          <h3>1. Nạp dữ liệu</h3>
          <label>
            Loại dữ liệu
            <select
              value={kind}
              onChange={(event) => {
                setKind(event.target.value);
                setFile(null);
              }}
            >
              <option value="text">Văn bản</option>
              <option value="image">Ảnh chart PNG/JPEG/WebP</option>
              <option value="document">PDF (cần parser từ xa)</option>
            </select>
          </label>
          <label>
            Tên nguồn *
            <input
              required
              maxLength={1000}
              value={source}
              onChange={(event) => setSource(event.target.value)}
              placeholder="Ví dụ: TEST-RAG-001"
            />
          </label>
          <label>
            {kind === "text" ? "Nội dung *" : "Mô tả bổ sung"}
            <textarea
              required={kind === "text"}
              rows={9}
              maxLength={100000}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Dán ghi chú, quy tắc, nội dung tài liệu…"
            />
          </label>
          {kind !== "text" && (
            <label>
              File *
              <input
                key={kind}
                type="file"
                required
                accept={kind === "image" ? "image/png,image/jpeg,image/webp" : "application/pdf"}
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
          )}
          <p>
            File tối đa 10 MB. Nạp lại y nguyên source + nội dung sẽ trả cùng document ID.
            PDF chỉ hoạt động khi backend có <code>RAG_REMOTE_PARSER_URL</code>.
          </p>
          <button className="primary" disabled={busy} type="submit">
            {busy ? "Đang gửi…" : "Nạp vào RAG"}
          </button>
        </form>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void action(async () => {
              if (!question.trim()) throw new Error("Nhập câu hỏi.");
              setAnswer("");
              setSources([]);
              setRerank({});
              const result = await request<QueryResult>("/query", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ question: question.trim(), mode }),
              });
              setAnswer(result.answer);
              setSources(result.sources ?? []);
              setRerank(result.rerank_metadata ?? {});
            });
          }}
        >
          <h3>2. Hỏi AI sau khi tài liệu COMPLETED</h3>
          <label>
            Chế độ retrieval
            <select value={mode} onChange={(event) => setMode(event.target.value)}>
              {MODES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Câu hỏi *
            <textarea
              required
              rows={7}
              maxLength={10000}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ví dụ: Theo tài liệu TEST-RAG-001, breakout cần kiểm tra gì?"
            />
          </label>
          <p>
            Nên nêu mã tài liệu hoặc mã xác minh trong câu hỏi để kiểm tra AI có bám nguồn.
            Nếu tài liệu chưa <code>COMPLETED</code>, câu trả lời có thể thiếu ngữ cảnh.
          </p>
          <button className="primary" disabled={busy} type="submit">
            {busy ? "Đang hỏi…" : "Hỏi RAG"}
          </button>

          {answer && (
            <div aria-live="polite" className="analysis-card">
              <h4>Câu trả lời</h4>
              <div style={{ whiteSpace: "pre-wrap" }}>{answer}</div>
              <h4>Nguồn trả về ({sources.length})</h4>
              {sources.length === 0 ? (
                <p className="muted">Không có source trong response.</p>
              ) : (
                <ul className="analysis-sources">
                  {sources.map((item, index) => {
                    const href = sourceHref(item);
                    const label = sourceLabel(item, index);
                    const snippet = sourceSnippet(item);
                    const sourceType =
                      typeof item.source_type === "string" ? item.source_type : "knowledge";
                    return (
                      <li key={`${href ?? label}-${index}`}>
                        {href?.startsWith("http") || href?.startsWith("/") ? (
                          <a href={href} target="_blank" rel="noreferrer">
                            {label}
                          </a>
                        ) : (
                          <span>{label}</span>
                        )}
                        <span className="muted"> · {sourceType}</span>
                        {snippet && <div className="muted">{snippet}</div>}
                      </li>
                    );
                  })}
                </ul>
              )}
              {Object.keys(rerank).length > 0 && (
                <details>
                  <summary>Rerank metadata</summary>
                  <pre>{JSON.stringify(rerank, null, 2)}</pre>
                </details>
              )}
            </div>
          )}
        </form>
      </div>

      {busy && <p role="status">Đang xử lý…</p>}
      {error && <p role="alert" className="rag-error">{error}</p>}

      <h3>3. Trạng thái tài liệu — phiên hiện tại</h3>
      {jobs.length === 0 && (
        <p>Chưa nạp tài liệu trong phiên này. Trạng thái gốc vẫn được lưu trong database.</p>
      )}
      {jobs.map((job) => (
        <div key={job.id} className="rag-job">
          <strong>{job.source}</strong> · {kindLabel(job.kind)} ·{" "}
          <span className={`badge ${job.status === "COMPLETED" ? "up" : job.status === "FAILED" ? "down" : "flat"}`}>
            {job.status}
          </span>
          {job.error_code && ` · ${job.error_code}`}
          <small>{job.id}</small>
          <button type="button" className="ghost" onClick={() => void refresh(job.id)}>
            Cập nhật
          </button>
          {(job.status === "FAILED" || job.status === "PARTIAL") && (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void action(async () => {
                  await request(`/documents/${job.id}/retry`, { method: "POST" });
                  await refresh(job.id);
                })
              }
            >
              Thử lại
            </button>
          )}
        </div>
      ))}

      <h3>4. Mẫu kiểm thử</h3>
      <p>
        Bấm mẫu để điền sẵn source + nội dung + câu hỏi, sau đó bấm <strong>Nạp vào RAG</strong>,
        chờ <code>COMPLETED</code>, rồi bấm <strong>Hỏi RAG</strong>.
      </p>
      {samples.map((sample) => (
        <button
          className="ghost"
          key={sample.source}
          type="button"
          onClick={() => {
            setKind("text");
            setFile(null);
            setSource(sample.source);
            setText(sample.text);
            setQuestion(sample.question);
          }}
        >
          {sample.name}
        </button>
      ))}

      <h3>Checklist kiểm thử AI</h3>
      <ol>
        <li>
          Nạp mẫu → chờ <code>COMPLETED</code> → hỏi câu đã điền. Kiểm tra câu trả lời có{" "}
          <code>SAOMAI-742</code> và có source.
        </li>
        <li>
          Hỏi diễn đạt khác: “Nhóm Sao Mai cần đối chiếu gì trước khi xác nhận phá đỉnh?” —
          kỳ vọng vẫn bám tài liệu.
        </li>
        <li>
          Hỏi ngoài dữ liệu: “DEMO tăng bao nhiêu sau 20 phiên?” — kỳ vọng thừa nhận chưa có
          kết quả, không bịa số.
        </li>
        <li>
          Nạp lại y nguyên source/nội dung: kỳ vọng cùng document ID, không tạo trùng.
        </li>
        <li>
          Ảnh: upload chart có đánh dấu rõ, hỏi vị trí đã đánh dấu; tự đối chiếu với ảnh gốc.
        </li>
        <li>
          Đổi mode <code>local/global/hybrid</code> để so sánh cách retrieval; mode hybrid là
          mặc định.
        </li>
      </ol>
      <p>
        Nếu báo RAG chưa bật: kiểm tra <code>RAG_ENABLED=true</code>, <code>/health</code> và
        restart API. Không nhập OpenRouter API key vào giao diện này.
      </p>
    </section>
  );
}
