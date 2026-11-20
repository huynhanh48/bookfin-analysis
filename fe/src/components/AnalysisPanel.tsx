import { useEffect, useState, type FormEvent } from "react";

import {
  createAnalysisCase,
  fetchAnalysisChart,
  getAnalysisCase,
  getAnalysisJob,
  queryMultimodalAnalysis,
  retryAnalysisCase,
  searchAnalysisCases,
  updateAnalysisOutcome,
} from "../api";
import { formatDateTime } from "../format";
import type {
  AnalysisCase,
  AnalysisJob,
  AnalysisSearchResponse,
  AnalysisSource,
  EvidenceAnswer,
  IndicatorConditionField,
  TechnicalClaim,
} from "../types";

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "FAILED", "NEEDS_REVIEW"]);
const CONDITION_FIELDS: IndicatorConditionField[] = [
  "rsi",
  "adx",
  "atr",
  "rvol",
  "obv",
  "macd",
  "macd_histogram",
];

interface ConditionRow {
  id: number;
  field: IndicatorConditionField;
  operator: "gte" | "lte" | "eq";
  value: string;
}

interface RecentCase {
  id: string;
  symbol: string | null;
  timeframe: string | null;
  analyzedAt: string | null;
  status: string;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function localDateTimeValue(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function toIso(value: string): string {
  return new Date(value).toISOString();
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Không đọc được file ảnh."));
    reader.readAsDataURL(file);
  });
}

function diagnosticList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function jobTone(status: string): "up" | "down" | "flat" {
  if (status === "COMPLETED") return "up";
  if (status === "FAILED") return "down";
  return "flat";
}

function claimTone(status: string): "up" | "down" | "flat" {
  if (status === "VERIFIED") return "up";
  if (status === "CONTRADICTED") return "down";
  return "flat";
}

function verificationTone(status: string): "up" | "down" | "flat" {
  if (status === "VERIFIED") return "up";
  if (status === "CONTRADICTED") return "down";
  return "flat";
}

function VerificationBadges({ summary }: { summary?: Record<string, number> }) {
  if (!summary || Object.keys(summary).length === 0) {
    return <span className="muted">Chưa có verification summary.</span>;
  }
  return (
    <div className="analysis-meta">
      {Object.entries(summary).map(([status, count]) => (
        <span key={status} className={`badge ${verificationTone(status)}`}>
          {status}: {count}
        </span>
      ))}
    </div>
  );
}

function ClaimsTable({ claims }: { claims: TechnicalClaim[] }) {
  if (claims.length === 0) {
    return <p className="muted">Chưa có claim (có thể job chưa chạy enrichment).</p>;
  }
  return (
    <div className="analysis-table-wrap">
      <table className="analysis-table">
        <thead>
          <tr>
            <th>Claim</th>
            <th>Nguồn</th>
            <th>Trạng thái</th>
            <th>Lý do / evidence</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((claim, index) => (
            <tr key={claim.claim_id ?? `${claim.claim_type}-${index}`}>
              <td>
                <strong>{claim.claim_type}</strong>
                <div className="muted">{claim.original_text}</div>
              </td>
              <td>{claim.source_type}</td>
              <td>
                <span className={`badge ${claimTone(claim.verification_status)}`}>
                  {claim.verification_status}
                </span>
              </td>
              <td>
                <div>{claim.verification_reason}</div>
                {claim.temporal_scope && (
                  <div className="muted">
                    scope: {claim.temporal_scope.kind}
                    {claim.temporal_scope.start ? ` · ${formatDateTime(claim.temporal_scope.start)}` : ""}
                    {claim.temporal_scope.end ? ` → ${formatDateTime(claim.temporal_scope.end)}` : ""}
                  </div>
                )}
                <div className="muted">{claim.evidence_ids.join(", ") || "—"}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function KeyValueGrid({ values }: { values?: Record<string, unknown> }) {
  if (!values || Object.keys(values).length === 0) return <span className="muted">—</span>;
  return (
    <dl className="analysis-kv">
      {Object.entries(values).map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value === null || value === undefined ? "—" : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function SourcesList({ sources }: { sources?: AnalysisSource[] }) {
  if (!sources || sources.length === 0) {
    return <p className="muted">Chưa có source.</p>;
  }
  return (
    <ul className="analysis-sources">
      {sources.map((source, index) => {
        const href = source.canonical_url ?? source.source_url;
        const label = source.title || href || source.evidence_id || `source-${index + 1}`;
        return (
          <li key={source.evidence_id ?? `${href}-${index}`}>
            {href?.startsWith("http") ? (
              <a href={href} target="_blank" rel="noreferrer">
                {label}
              </a>
            ) : (
              <span>{label}</span>
            )}
            <span className="muted">
              {source.source_type ? ` · ${source.source_type}` : ""}
              {source.publisher ? ` · ${source.publisher}` : ""}
              {source.published_at ? ` · ${formatDateTime(source.published_at)}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export default function AnalysisPanel() {
  const [adminKey, setAdminKey] = useState("");
  const [createError, setCreateError] = useState("");
  const [createBusy, setCreateBusy] = useState(false);

  const [symbol, setSymbol] = useState("");
  const [timeframe, setTimeframe] = useState("1D");
  const [analyzedAt, setAnalyzedAt] = useState(localDateTimeValue());
  const [thesis, setThesis] = useState("");
  const [source, setSource] = useState("TradingView test");
  const [company, setCompany] = useState("");
  const [companyAliases, setCompanyAliases] = useState("");
  const [sector, setSector] = useState("");
  const [exchange, setExchange] = useState("");
  const [priceBasis, setPriceBasis] = useState("RAW");
  const [chartFile, setChartFile] = useState<File | null>(null);

  const [job, setJob] = useState<AnalysisJob | null>(null);
  const [caseData, setCaseData] = useState<AnalysisCase | null>(null);
  const [caseId, setCaseId] = useState("");
  const [caseIdInput, setCaseIdInput] = useState("");
  const [recentCases, setRecentCases] = useState<RecentCase[]>([]);
  const [copyState, setCopyState] = useState("");
  const [chartUrl, setChartUrl] = useState("");
  const [outcomeText, setOutcomeText] = useState("{}");
  const [outcomeBusy, setOutcomeBusy] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);

  const [query, setQuery] = useState("");
  const [searchImage, setSearchImage] = useState<File | null>(null);
  const [searchSymbol, setSearchSymbol] = useState("");
  const [searchTimeframe, setSearchTimeframe] = useState("");
  const [searchTopK, setSearchTopK] = useState("5");
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  const [conditions, setConditions] = useState<ConditionRow[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [searchResult, setSearchResult] = useState<AnalysisSearchResponse | null>(null);

  const [question, setQuestion] = useState("");
  const [queryImage, setQueryImage] = useState<File | null>(null);
  const [queryBusy, setQueryBusy] = useState(false);
  const [queryError, setQueryError] = useState("");
  const [answer, setAnswer] = useState<EvidenceAnswer | null>(null);

  function rememberCase(item: AnalysisCase) {
    const entry: RecentCase = {
      id: item.id,
      symbol: item.symbol,
      timeframe: item.timeframe,
      analyzedAt: item.analyzed_at,
      status: item.ingest_status,
    };
    setRecentCases((current) => {
      const next = [entry, ...current.filter((row) => row.id !== item.id)].slice(0, 8);
      try {
        window.localStorage.setItem("bookfin.recentAnalysisCases", JSON.stringify(next));
      } catch {
        // localStorage may be unavailable in private mode; keep session state only.
      }
      return next;
    });
  }

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem("bookfin.recentAnalysisCases");
      if (!raw) return;
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        setRecentCases(
          parsed
            .filter((item): item is RecentCase => typeof item === "object" && item !== null && "id" in item)
            .slice(0, 8),
        );
      }
    } catch {
      setRecentCases([]);
    }
  }, []);

  async function loadCase(id: string) {
    setCreateError("");
    try {
      const response = await getAnalysisCase(id, adminKey);
      setCaseData(response.data);
      setCaseId(response.data.id);
      setCaseIdInput(response.data.id);
      setOutcomeText(JSON.stringify(response.data.outcome ?? {}, null, 2));
      rememberCase(response.data);
    } catch (error) {
      setCreateError(errorText(error));
    }
  }

  useEffect(() => {
    if (!job || TERMINAL.has(job.status)) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const latest = await getAnalysisJob(job.id, adminKey);
        if (cancelled) return;
        setJob(latest.data);
        if (TERMINAL.has(latest.data.status)) {
          const id = latest.data.case_id ?? caseId;
          if (id) {
            const detail = await getAnalysisCase(id, adminKey);
            if (!cancelled) {
              setCaseData(detail.data);
              setCaseId(detail.data.id);
              setOutcomeText(JSON.stringify(detail.data.outcome ?? {}, null, 2));
              rememberCase(detail.data);
            }
          }
        }
      } catch (error) {
        if (!cancelled) setCreateError(errorText(error));
      }
    };
    const timer = window.setInterval(() => {
      void tick();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [job?.id, job?.status, adminKey, caseId]);

  useEffect(() => {
    if (!caseData?.chart_path) {
      setChartUrl("");
      return;
    }
    let cancelled = false;
    let created = "";
    fetchAnalysisChart(caseData.id, adminKey)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        created = url;
        setChartUrl(url);
      })
      .catch(() => {
        if (!cancelled) setChartUrl("");
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [caseData?.id, caseData?.chart_path, adminKey]);

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateError("");
    setCreateBusy(true);
    try {
      if (!thesis.trim()) throw new Error("Nhập ghi chú phân tích.");
      if (!analyzedAt) throw new Error("Chọn thời điểm phân tích.");
      if (chartFile && chartFile.size > 10 * 1024 * 1024) {
        throw new Error("Ảnh chart tối đa 10 MB.");
      }
      const metadata: Record<string, unknown> = {
        thesis: thesis.trim(),
        source: source.trim() || "TradingView test",
        price_basis: priceBasis,
        analyzed_at: toIso(analyzedAt),
      };
      if (symbol.trim()) metadata.symbol = symbol.trim().toUpperCase();
      if (timeframe) metadata.timeframe = timeframe;
      if (company.trim()) metadata.company_name = company.trim();
      const aliases = companyAliases
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      if (aliases.length > 0) metadata.company_aliases = aliases;
      if (sector.trim()) metadata.sector = sector.trim();
      if (exchange.trim()) metadata.exchange = exchange.trim().toUpperCase();
      const response = await createAnalysisCase(metadata, chartFile, adminKey);
      setCaseData(response.data);
      setCaseId(response.data.id);
      setCaseIdInput(response.data.id);
      setOutcomeText(JSON.stringify(response.data.outcome ?? {}, null, 2));
      rememberCase(response.data);
      if (response.job_id) {
        const jobResponse = await getAnalysisJob(response.job_id, adminKey);
        setJob(jobResponse.data);
      } else {
        setJob(null);
      }
    } catch (error) {
      setCreateError(errorText(error));
    } finally {
      setCreateBusy(false);
    }
  }

  function addCondition() {
    setConditions((current) => [
      ...current,
      { id: Date.now(), field: "rsi", operator: "gte", value: "50" },
    ]);
  }

  function updateCondition(id: number, patch: Partial<ConditionRow>) {
    setConditions((current) =>
      current.map((condition) => (condition.id === id ? { ...condition, ...patch } : condition)),
    );
  }

  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearchError("");
    setSearchBusy(true);
    try {
      if (!query.trim() && !searchImage) {
        throw new Error("Nhập text hoặc chọn ảnh chart để tìm.");
      }
      if (searchImage && searchImage.size > 7 * 1024 * 1024) {
        throw new Error("Ảnh query tối đa 7 MB (JSON base64 có thể vượt giới hạn body).");
      }
      if (before && after && new Date(after) >= new Date(before)) {
        throw new Error("Thời gian 'sau' phải nhỏ hơn 'trước'.");
      }
      const payload: Record<string, unknown> = { top_k: Number(searchTopK) || 5 };
      if (query.trim()) payload.query = query.trim();
      if (searchSymbol.trim()) payload.symbol = searchSymbol.trim().toUpperCase();
      if (searchTimeframe) payload.timeframe = searchTimeframe;
      if (before) payload.before = toIso(before);
      if (after) payload.after = toIso(after);
      if (searchImage) payload.image = await fileToDataUrl(searchImage);
      if (conditions.length > 0) {
        payload.conditions = conditions.map((condition) => {
          const value = Number(condition.value);
          if (!Number.isFinite(value)) {
            throw new Error(`Giá trị điều kiện ${condition.field} không hợp lệ.`);
          }
          return { field: condition.field, operator: condition.operator, value };
        });
      }
      const response = await searchAnalysisCases(payload, adminKey);
      setSearchResult(response);
    } catch (error) {
      setSearchError(errorText(error));
    } finally {
      setSearchBusy(false);
    }
  }

  async function submitQuery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQueryError("");
    setQueryBusy(true);
    try {
      if (!question.trim()) throw new Error("Nhập câu hỏi.");
      if (queryImage && queryImage.size > 10 * 1024 * 1024) {
        throw new Error("Ảnh query tối đa 10 MB.");
      }
      const metadata: Record<string, unknown> = {
        query: question.trim(),
        top_k: Number(searchTopK) || 5,
      };
      if (searchSymbol.trim()) metadata.symbol = searchSymbol.trim().toUpperCase();
      if (searchTimeframe) metadata.timeframe = searchTimeframe;
      const response = await queryMultimodalAnalysis(metadata, queryImage, adminKey);
      setAnswer(response.data);
    } catch (error) {
      setQueryError(errorText(error));
    } finally {
      setQueryBusy(false);
    }
  }

  async function retryCase() {
    if (!caseData) return;
    setRetryBusy(true);
    setCreateError("");
    try {
      const response = await retryAnalysisCase(caseData.id, adminKey);
      setCaseData(response.data);
      if (job) {
        setJob({
          ...job,
          status: "PENDING",
          stage: "PENDING",
          progress: 0,
          error_code: null,
        });
      }
    } catch (error) {
      setCreateError(errorText(error));
    } finally {
      setRetryBusy(false);
    }
  }

  async function saveOutcome() {
    if (!caseData) return;
    setOutcomeBusy(true);
    setCreateError("");
    try {
      const parsed: unknown = JSON.parse(outcomeText);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Outcome phải là JSON object.");
      }
      const response = await updateAnalysisOutcome(
        caseData.id,
        parsed as Record<string, unknown>,
        adminKey,
      );
      setCaseData(response.data);
      setOutcomeText(JSON.stringify(response.data.outcome ?? {}, null, 2));
    } catch (error) {
      setCreateError(errorText(error));
    } finally {
      setOutcomeBusy(false);
    }
  }

  const enrichment = caseData?.enrichment;
  const technical = enrichment?.technical_snapshot;
  const diagnosticWarnings = diagnosticList(enrichment?.diagnostics?.warnings);
  const newsWarnings = diagnosticList(enrichment?.diagnostics?.news_warnings);
  const diagnosticNotes = diagnosticList(enrichment?.diagnostics?.notes);

  return (
    <section className="rag-panel analysis-panel">
      <h2>Analysis Case Memory — kiểm chứng và tìm case tương tự</h2>
      <p>
        Upload chart + ghi chú để chạy Vision → claim verification → technical/news evidence →
        dual embedding. Thesis gốc và outcome được lưu tách biệt; không tự động ingest ảnh query.
      </p>
      <label>
        Admin key (nếu backend yêu cầu)
        <input
          type="password"
          autoComplete="off"
          value={adminKey}
          onChange={(event) => setAdminKey(event.target.value)}
        />
      </label>

      <div className="analysis-loader">
        <h3>Mở case đã có</h3>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const id = caseIdInput.trim();
            if (id) void loadCase(id);
          }}
        >
          <input
            value={caseIdInput}
            onChange={(event) => setCaseIdInput(event.target.value)}
            placeholder="Dán case UUID, ví dụ a2f924f4-..."
          />
          <button className="primary" type="submit">
            Mở case
          </button>
        </form>
        {recentCases.length > 0 && (
          <div className="analysis-recent">
            <strong>Case gần đây</strong>
            {recentCases.map((item) => (
              <button
                key={item.id}
                type="button"
                className="ghost"
                onClick={() => void loadCase(item.id)}
              >
                {item.symbol ?? "?"} · {item.timeframe ?? "?"} · {item.status} ·{" "}
                {formatDateTime(item.analyzedAt)}
              </button>
            ))}
          </div>
        )}
      </div>

      <details open className="analysis-section">
        <summary>1. Tạo case từ chart + ghi chú</summary>
        <div className="rag-columns">
          <form onSubmit={(event) => void submitCreate(event)}>
            <div className="analysis-form-grid">
              <label>
                Mã (tùy chọn)
                <input
                  value={symbol}
                  onChange={(event) => setSymbol(event.target.value.toUpperCase())}
                  placeholder="KDH"
                  maxLength={20}
                />
              </label>
              <label>
                Khung
                <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
                  <option value="1D">D1</option>
                  <option value="1W">W1</option>
                </select>
              </label>
              <label>
                Thời điểm phân tích *
                <input
                  type="datetime-local"
                  required
                  value={analyzedAt}
                  onChange={(event) => setAnalyzedAt(event.target.value)}
                />
                <span className="muted analysis-hint">
                  Mốc as-of: chỉ dùng nến đã đóng và tin trước thời điểm này.
                </span>
              </label>
              <label>
                Nguồn
                <input value={source} onChange={(event) => setSource(event.target.value)} />
              </label>
              <label>
                Công ty
                <input value={company} onChange={(event) => setCompany(event.target.value)} />
              </label>
              <label>
                Tên thay thế (cách nhau bằng dấu phẩy)
                <input
                  value={companyAliases}
                  onChange={(event) => setCompanyAliases(event.target.value)}
                  placeholder="KDH Group, Khang Dien House"
                />
              </label>
              <label>
                Ngành
                <input value={sector} onChange={(event) => setSector(event.target.value)} />
              </label>
              <label>
                Sàn
                <input
                  value={exchange}
                  onChange={(event) => setExchange(event.target.value.toUpperCase())}
                  placeholder="HOSE"
                />
              </label>
              <label>
                Price basis
                <select value={priceBasis} onChange={(event) => setPriceBasis(event.target.value)}>
                  <option value="RAW">RAW</option>
                  <option value="ADJUSTED">ADJUSTED</option>
                </select>
              </label>
            </div>
            <label>
              Ghi chú phân tích (thesis gốc) *
              <textarea
                required
                rows={8}
                maxLength={50_000}
                value={thesis}
                onChange={(event) => setThesis(event.target.value)}
                placeholder="VD: KDH có phân kỳ dương RSI, engulfing, dòng tiền trở lại."
              />
            </label>
            <label>
              Chart image (PNG/JPEG/WebP)
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={(event) => setChartFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <button className="primary" disabled={createBusy} type="submit">
              {createBusy ? "Đang gửi…" : "Gửi case (202 + job)"}
            </button>
            {createError && <p className="rag-error">{createError}</p>}
          </form>

          <div>
            {job && (
              <div className="analysis-card">
                <div className="analysis-card-head">
                  <h4>Job {job.id.slice(0, 8)}…</h4>
                  <span className={`badge ${jobTone(job.status)}`}>{job.status}</span>
                </div>
                <div className="analysis-meta">
                  <span>stage: {job.stage}</span>
                  <span>attempts: {job.attempts}</span>
                  {job.case_id && <span>case: {job.case_id.slice(0, 8)}…</span>}
                </div>
                <progress className="analysis-progress" max={100} value={job.progress} />
                {job.error_code && <p className="rag-error">{job.error_code}</p>}
                <details>
                  <summary>Diagnostics</summary>
                  <pre>{JSON.stringify(job.diagnostics, null, 2)}</pre>
                </details>
              </div>
            )}

            {caseData && (
              <div className="analysis-card">
                <div className="analysis-card-head">
                  <h4>
                    {caseData.symbol ?? "?"} · {caseData.timeframe ?? "?"}
                  </h4>
                  <span className={`badge ${jobTone(caseData.ingest_status)}`}>
                    {caseData.ingest_status}
                  </span>
                </div>
                <p className="muted">
                  Phân tích: {formatDateTime(caseData.analyzed_at)} · Nạp lúc:{" "}
                  {formatDateTime(caseData.created_at)} · {caseData.source}
                </p>
                <p className="muted analysis-case-id">
                  case: {caseData.id}{" "}
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => {
                      void navigator.clipboard?.writeText(caseData.id);
                      setCopyState("Đã copy case ID");
                      window.setTimeout(() => setCopyState(""), 1500);
                    }}
                  >
                    Copy ID
                  </button>
                  <button type="button" className="ghost" onClick={() => void loadCase(caseData.id)}>
                    Refresh case
                  </button>
                  {copyState && <span>{copyState}</span>}
                </p>
                {(caseData.ingest_status === "PARTIAL" || caseData.ingest_status === "FAILED") && (
                  <button type="button" disabled={retryBusy} onClick={() => void retryCase()}>
                    {retryBusy ? "Đang retry…" : "Retry enrichment"}
                  </button>
                )}
                <h4>Original thesis</h4>
                <p className="analysis-thesis">{caseData.thesis}</p>
                {chartUrl && (
                  <img className="analysis-chart" src={chartUrl} alt="Chart gốc của case" />
                )}
                <h4>Verification summary</h4>
                <VerificationBadges summary={enrichment?.verification_summary} />
                {(diagnosticWarnings.length > 0 ||
                  newsWarnings.length > 0 ||
                  diagnosticNotes.length > 0) && (
                  <div className="analysis-meta">
                    {diagnosticWarnings.map((item) => (
                      <span key={`w-${item}`} className="badge down">
                        warning: {item}
                      </span>
                    ))}
                    {newsWarnings.map((item) => (
                      <span key={`n-${item}`} className="badge flat">
                        news: {item}
                      </span>
                    ))}
                    {diagnosticNotes.map((item) => (
                      <span key={`note-${item}`} className="badge flat">
                        note: {item}
                      </span>
                    ))}
                  </div>
                )}
                <h4>Resolved context</h4>
                <KeyValueGrid values={enrichment?.resolved_context} />
                <h4>Claims</h4>
                <ClaimsTable claims={enrichment?.claims ?? []} />
                <h4>Vision observations (chưa xác minh)</h4>
                {enrichment?.vision_observations?.description ? (
                  <p>{enrichment.vision_observations.description}</p>
                ) : (
                  <p className="muted">Chưa có vision observations.</p>
                )}
                {enrichment?.vision_observations?.uncertainties?.length ? (
                  <p className="muted">
                    Uncertainties: {enrichment.vision_observations.uncertainties.join("; ")}
                  </p>
                ) : null}
                <h4>Technical evidence</h4>
                <div className="analysis-meta">
                  <span>status: {technical?.status ?? "—"}</span>
                  <span>as_of: {formatDateTime(technical?.as_of)}</span>
                  <span>bars: {technical?.bars ?? "—"}</span>
                  <span>engine: {technical?.engine_version ?? "—"}</span>
                </div>
                <KeyValueGrid values={technical?.filter_values} />
                <p className="muted">
                  Trend: {technical?.market_structure?.confirmed_trend ?? "—"} · RSI at
                  analysis: {technical?.rsi_at_analysis ?? technical?.filter_values?.rsi ?? "—"} ·
                  Divergence events: {technical?.divergence_events?.length ?? 0} · Structure
                  events: {technical?.structure_events?.length ?? 0}
                </p>
                <details>
                  <summary>Divergence events / RSI context</summary>
                  <pre>{JSON.stringify(technical?.divergence_events ?? [], null, 2)}</pre>
                </details>
                <h4>Sources</h4>
                <SourcesList sources={enrichment?.sources} />
                <h4>Embeddings</h4>
                {enrichment?.embeddings?.length ? (
                  <div className="analysis-table-wrap">
                    <table className="analysis-table">
                      <thead>
                        <tr>
                          <th>Kind</th>
                          <th>Model</th>
                          <th>Dim</th>
                          <th>Content hash</th>
                          <th>Image hash</th>
                        </tr>
                      </thead>
                      <tbody>
                        {enrichment.embeddings.map((embedding) => (
                          <tr key={`${embedding.kind}-${embedding.model}`}>
                            <td>{embedding.kind}</td>
                            <td>{embedding.model}</td>
                            <td>{embedding.dimension}</td>
                            <td>{embedding.content_hash ?? "—"}</td>
                            <td>{embedding.image_hash ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="muted">
                    Chưa có embedding metadata. Kiểm tra diagnostics hoặc retry case.
                  </p>
                )}
                {enrichment?.normalized_text && (
                  <details>
                    <summary>Normalized text</summary>
                    <pre>{enrichment.normalized_text}</pre>
                  </details>
                )}
                <details>
                  <summary>Enrichment diagnostics</summary>
                  <pre>{JSON.stringify(enrichment?.diagnostics ?? {}, null, 2)}</pre>
                </details>
                <h4>Outcome (ghi sau, không sửa thesis)</h4>
                <p className="muted">
                  Outcome là kết quả sau này (5/20 phiên…), không tham gia embedding/search.
                  Để trống <code>{}</code> cũng được. Merge là top-level.
                </p>
                <textarea
                  rows={8}
                  value={outcomeText}
                  onChange={(event) => setOutcomeText(event.target.value)}
                />
                <button
                  type="button"
                  className="ghost"
                  onClick={() =>
                    setOutcomeText(
                      JSON.stringify(
                        {
                          entry: { price: null, at: caseData.analyzed_at },
                          horizons: {
                            "5_sessions": {
                              return_pct: null,
                              max_drawdown_pct: null,
                              hit_target: null,
                            },
                            "20_sessions": {
                              return_pct: null,
                              max_drawdown_pct: null,
                              hit_target: null,
                            },
                          },
                          verdict: "",
                          notes: "",
                        },
                        null,
                        2,
                      ),
                    )
                  }
                >
                  Điền mẫu outcome
                </button>
                <button type="button" disabled={outcomeBusy} onClick={() => void saveOutcome()}>
                  {outcomeBusy ? "Đang lưu…" : "Lưu outcome"}
                </button>
              </div>
            )}
          </div>
        </div>
      </details>

      <details className="analysis-section">
        <summary>2. Tìm case tương tự (text/ảnh + filter)</summary>
        <form onSubmit={(event) => void submitSearch(event)}>
          <label>
            Query text
            <textarea
              rows={4}
              maxLength={10_000}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="RSI phân kỳ dương, engulfing, volume tăng…"
            />
          </label>
          <label>
            Ảnh query (tùy chọn)
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => setSearchImage(event.target.files?.[0] ?? null)}
            />
          </label>
          <div className="analysis-form-grid">
            <label>
              Mã
              <input
                value={searchSymbol}
                onChange={(event) => setSearchSymbol(event.target.value.toUpperCase())}
                placeholder="KDH"
              />
            </label>
            <label>
              Khung
              <select
                value={searchTimeframe}
                onChange={(event) => setSearchTimeframe(event.target.value)}
              >
                <option value="">Tất cả</option>
                <option value="1D">D1</option>
                <option value="1W">W1</option>
              </select>
            </label>
            <label>
              Top K
              <input
                type="number"
                min={1}
                max={50}
                value={searchTopK}
                onChange={(event) => setSearchTopK(event.target.value)}
              />
            </label>
            <label>
              Trước (before, exclusive)
              <input
                type="datetime-local"
                value={before}
                onChange={(event) => setBefore(event.target.value)}
              />
            </label>
            <label>
              Sau (after, inclusive)
              <input
                type="datetime-local"
                value={after}
                onChange={(event) => setAfter(event.target.value)}
              />
            </label>
          </div>

          <div className="analysis-conditions">
            <strong>Điều kiện chỉ báo (SQL filter chính xác)</strong>
            {conditions.map((condition) => (
              <div className="analysis-condition" key={condition.id}>
                <select
                  value={condition.field}
                  onChange={(event) =>
                    updateCondition(condition.id, {
                      field: event.target.value as IndicatorConditionField,
                    })
                  }
                >
                  {CONDITION_FIELDS.map((field) => (
                    <option key={field} value={field}>
                      {field}
                    </option>
                  ))}
                </select>
                <select
                  value={condition.operator}
                  onChange={(event) =>
                    updateCondition(condition.id, {
                      operator: event.target.value as ConditionRow["operator"],
                    })
                  }
                >
                  <option value="gte">≥</option>
                  <option value="lte">≤</option>
                  <option value="eq">=</option>
                </select>
                <input
                  type="number"
                  step="any"
                  value={condition.value}
                  onChange={(event) =>
                    updateCondition(condition.id, { value: event.target.value })
                  }
                />
                <button
                  type="button"
                  className="ghost"
                  onClick={() =>
                    setConditions((current) =>
                      current.filter((item) => item.id !== condition.id),
                    )
                  }
                >
                  Xóa
                </button>
              </div>
            ))}
            <button type="button" className="ghost" onClick={addCondition}>
              + Thêm điều kiện
            </button>
          </div>

          <button className="primary" disabled={searchBusy} type="submit">
            {searchBusy ? "Đang tìm…" : "Tìm case tương tự"}
          </button>
          {searchError && <p className="rag-error">{searchError}</p>}
        </form>

        {searchResult && (
          <div className="analysis-results">
            <div className="analysis-meta">
              <span>{searchResult.data.length} kết quả</span>
              <VerificationBadges summary={searchResult.verification_summary} />
            </div>
            {searchResult.data.map((match) => (
              <div className="analysis-card" key={match.case.id}>
                <div className="analysis-card-head">
                  <h4>
                    {match.case.symbol ?? "?"} · {match.case.timeframe ?? "?"}
                  </h4>
                  <span className="badge flat">sim {match.similarity.toFixed(3)}</span>
                </div>
                <p className="muted">
                  {formatDateTime(match.case.analyzed_at)} · {match.case.ingest_status} · fusion{" "}
                  {match.fusion_score?.toFixed(4) ?? "—"} · rerank{" "}
                  {match.rerank_score?.toFixed(3) ?? "—"}
                </p>
                <p>{match.case.thesis.slice(0, 500)}</p>
                <VerificationBadges summary={match.case.enrichment?.verification_summary} />
                <button type="button" onClick={() => void loadCase(match.case.id)}>
                  Xem case
                </button>
              </div>
            ))}
            <details>
              <summary>Retrieval metadata</summary>
              <pre>{JSON.stringify(searchResult.retrieval_metadata, null, 2)}</pre>
            </details>
            <details>
              <summary>Rerank metadata</summary>
              <pre>{JSON.stringify(searchResult.rerank_metadata, null, 2)}</pre>
            </details>
          </div>
        )}
      </details>

      <details className="analysis-section">
        <summary>3. Hỏi multimodal có citation</summary>
        <form onSubmit={(event) => void submitQuery(event)}>
          <label>
            Câu hỏi *
            <textarea
              required
              rows={4}
              maxLength={10_000}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="So sánh case này với các lần phân tích KDH trước đây…"
            />
          </label>
          <label>
            Ảnh chart (tùy chọn; không được ingest)
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => setQueryImage(event.target.files?.[0] ?? null)}
            />
          </label>
          <button className="primary" disabled={queryBusy} type="submit">
            {queryBusy ? "Đang hỏi…" : "Hỏi multimodal"}
          </button>
          {queryError && <p className="rag-error">{queryError}</p>}
        </form>

        {answer && (
          <div className="analysis-card">
            <h4>Câu trả lời</h4>
            <p className="analysis-answer">{answer.answer}</p>
            <div className="analysis-meta">
              <span>case_ids: {answer.case_ids.length}</span>
              <span>sources: {answer.sources.length}</span>
            </div>
            <SourcesList sources={answer.sources} />
            <details>
              <summary>Verification summary</summary>
              <pre>{JSON.stringify(answer.verification_summary, null, 2)}</pre>
            </details>
            <details>
              <summary>Retrieval metadata</summary>
              <pre>{JSON.stringify(answer.retrieval_metadata, null, 2)}</pre>
            </details>
            <details>
              <summary>Rerank metadata</summary>
              <pre>{JSON.stringify(answer.rerank_metadata, null, 2)}</pre>
            </details>
          </div>
        )}
      </details>
    </section>
  );
}
