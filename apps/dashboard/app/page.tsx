"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Profile = "PROACTIVE" | "BALANCED" | "CAUTIOUS";

const profiles: Array<{ id: Profile; label: string; description: string }> = [
  { id: "PROACTIVE", label: "Chủ động", description: "Ưu tiên cơ hội sớm" },
  { id: "BALANCED", label: "Cân bằng", description: "Cân bằng cơ hội và bằng chứng" },
  { id: "CAUTIOUS", label: "Thận trọng", description: "Chọn lọc và giới hạn rủi ro" },
];

type Snapshot = {
  snapshot_id: string;
  symbol: string;
  decision_time: string;
  data_mode: string;
  quality_status: string;
  features: Record<string, { value: string | number | boolean | null }>;
};

type SnapshotResponse = { snapshots: Snapshot[] };

type DecisionRecord = {
  decision_id: string;
  snapshot_id: string;
  profile: Profile;
  decision: "LONG" | "SHORT" | "WAIT" | "NO_TRADE";
  selected_candidate_id: string | null;
  watch_candidate_id: string | null;
  condition_ids: string[];
  reason_codes: string[];
  summary_vi: string;
  validation_status: "VALID" | "INVALID" | "SERVICE_ERROR";
  candidate?: {
    strategy: string;
    entry_stage: string;
    side: "LONG" | "SHORT";
    entry_estimate: number;
    stop_price: number;
    target_price: number;
    statistics_status: string;
  } | null;
  risk?: {
    allowed?: boolean;
    codes?: string[];
    risk_budget_usdt?: number;
    quantity_allowed?: number;
  } | null;
  created_at?: string;
};

type DecisionResponse = { decisions: DecisionRecord[] };

type HealthResponse = {
  status: "ok";
  service: "trade-brain";
  execution_mode: "PAPER";
  real_money_enabled: false;
};

type PaperRecommendation = {
  recommendation_id: string;
  profile: Profile;
  symbol: string | null;
  side: "LONG" | "SHORT";
  state: string;
  data_quality: "VALID" | "DEGRADED" | "AMBIGUOUS" | "INVALID";
  quantity: string;
  entry_fill: string | null;
  exit_fill: string | null;
  funding_pnl: string;
  outcome: string | null;
  net_pnl: string | null;
  emitted_at: string;
};

type PaperResponse = { recommendations: PaperRecommendation[] };

type PaperAccount = {
  profile: Profile;
  initial_equity: string;
  current_equity: string;
  mark_to_market_equity: string;
  realized_pnl: string;
};

type AccountResponse = { accounts: PaperAccount[] };

type PaperReport = {
  profile: Profile;
  recommendation_count: number;
  closed_count: number;
  net_positive_rate: string | null;
  total_net_pnl: string;
  max_drawdown: string;
  cohort_status: string;
};

type GlobalReport = Omit<PaperReport, "profile"> & { profile: null };
type ReportResponse = {
  reports: Array<PaperReport | null>;
  completed_trade_reports: Array<PaperReport | null>;
  global_setup_report: GlobalReport | null;
};

type EvaluationResponse = {
  status: "ok";
  message: string;
  evaluated_snapshot_ids: string[];
  decision_count: number;
  recommendation_count: number;
  result_summary?: EvaluationSummaryItem[];
};

type EvaluationSummaryDecision = {
  profile: Profile;
  decision: string;
  icon: string;
  summary_vi: string;
  reasons: string[];
  candidate?: {
    strategy?: string;
    entry_stage?: string;
    side?: string;
    entry_estimate?: number;
    stop_price?: number;
    target_price?: number;
  } | null;
};

type EvaluationSummaryItem = {
  symbol: string;
  snapshot_id: string;
  candidate_count: number;
  eligible_count: number;
  decisions: EvaluationSummaryDecision[];
};

type EvaluationStatus = {
  run_id: string | null;
  status: "IDLE" | "RUNNING" | "COMPLETED" | "FAILED";
  started_at: string | null;
  finished_at: string | null;
  total_count: number;
  completed_count: number;
  current_index: number;
  current_symbol: string | null;
  symbols: string[];
  result_summary: EvaluationSummaryItem[];
  error: string | null;
};

type EvaluationStatusResponse = { evaluation: EvaluationStatus };

function isSnapshotResponse(value: unknown): value is SnapshotResponse {
  if (!value || typeof value !== "object" || !("snapshots" in value)) return false;
  return Array.isArray(value.snapshots);
}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (!value || typeof value !== "object") return false;
  const health = value as Partial<HealthResponse>;
  return (
    health.status === "ok" &&
    health.service === "trade-brain" &&
    health.execution_mode === "PAPER" &&
    health.real_money_enabled === false
  );
}

function isDecisionResponse(value: unknown): value is DecisionResponse {
  if (!value || typeof value !== "object" || !("decisions" in value)) return false;
  return Array.isArray(value.decisions);
}

function isPaperResponse(value: unknown): value is PaperResponse {
  if (!value || typeof value !== "object" || !("recommendations" in value)) return false;
  return Array.isArray(value.recommendations);
}

function isAccountResponse(value: unknown): value is AccountResponse {
  if (!value || typeof value !== "object" || !("accounts" in value)) return false;
  return Array.isArray(value.accounts);
}

function isReportResponse(value: unknown): value is ReportResponse {
  if (!value || typeof value !== "object" || !("reports" in value)) return false;
  return Array.isArray(value.reports);
}

function isEvaluationStatusResponse(value: unknown): value is EvaluationStatusResponse {
  if (!value || typeof value !== "object" || !("evaluation" in value)) return false;
  const evaluation = value.evaluation;
  return Boolean(evaluation && typeof evaluation === "object" && "status" in evaluation);
}

function formatSnapshotTime(value: string): string {
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function snapshotQualityLabel(snapshot: Snapshot): string {
  const featureCount = Object.keys(snapshot.features ?? {}).length;
  return `${snapshot.quality_status} · ${featureCount} biến`;
}

function snapshotFeatureSummary(snapshot: Snapshot): string {
  const features = snapshot.features ?? {};
  const summary: string[] = [];
  const close = features.close_15m?.value;
  const atr = features.atr_15m?.value;
  const structure = features.structure_state_15m?.value;
  const spread = features.spread_bps?.value;
  const rangePosition = features.range_position_15m?.value;
  if (typeof close === "number") summary.push(`C ${close}`);
  if (typeof atr === "number") summary.push(`ATR ${atr}`);
  if (typeof structure === "string") summary.push(structure);
  if (typeof spread === "number") summary.push(`Spread ${spread.toFixed(1)} bps`);
  if (typeof rangePosition === "number") summary.push(`Range ${(rangePosition * 100).toFixed(0)}%`);
  return summary.join(" · ") || "Chưa có feature tóm tắt";
}

function formatRatio(value: string | null | undefined, emptyLabel: string): string {
  if (value === null || value === undefined) return emptyLabel;
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? `${(numericValue * 100).toFixed(1)}%` : emptyLabel;
}

function PaginationControls({
  page,
  pageCount,
  onChange,
}: {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
}) {
  if (pageCount <= 1) return null;
  return (
    <div className="pagination" aria-label="Phân trang">
      <button type="button" className="symbol-action" disabled={page === 1} onClick={() => onChange(page - 1)}>Trước</button>
      <span>Trang {page}/{pageCount}</span>
      <button type="button" className="symbol-action" disabled={page === pageCount} onClick={() => onChange(page + 1)}>Sau</button>
    </div>
  );
}

type HistoryTab = "decisions" | "snapshots" | "journal" | "queue";

const historyTabLabels: Array<{ id: HistoryTab; label: string; description: string }> = [
  { id: "decisions", label: "Quyết định", description: "Claude audit" },
  { id: "snapshots", label: "Snapshots", description: "Dữ liệu thị trường" },
  { id: "journal", label: "Paper journal", description: "Kết quả mô phỏng" },
  { id: "queue", label: "Queue audit", description: "Lần quét gần nhất" },
];

function profileLabel(profile: Profile): string {
  return profiles.find((item) => item.id === profile)?.label ?? profile;
}

function statusTone(value: string): "good" | "warn" | "bad" | "info" {
  const normalized = value.toUpperCase();
  if (["VALID", "LONG", "SHORT", "CLOSED_TP", "RECOMMENDED"].includes(normalized)) return "good";
  if (["WAIT", "DEGRADED", "AMBIGUOUS", "OPEN"].includes(normalized)) return "warn";
  if (["NO_TRADE", "INVALID", "SERVICE_ERROR", "CLOSED_SL"].includes(normalized)) return "bad";
  return "info";
}

function StatusBadge({ value }: { value: string }) {
  return <span className={`history-badge ${statusTone(value)}`}>{value.replaceAll("_", " ")}</span>;
}

function HistoryWorkspace({
  selectedProfile,
  decisions,
  snapshots,
  recommendations,
  lastEvaluation,
  isLoading,
  connectionError,
}: {
  selectedProfile: Profile;
  decisions: DecisionRecord[];
  snapshots: Snapshot[];
  recommendations: PaperRecommendation[];
  lastEvaluation: EvaluationSummaryItem[];
  isLoading: boolean;
  connectionError: string | null;
}) {
  const [activeTab, setActiveTab] = useState<HistoryTab>("decisions");
  const [search, setSearch] = useState("");
  const [decisionFilter, setDecisionFilter] = useState("ALL");
  const [qualityFilter, setQualityFilter] = useState("ALL");
  const [stateFilter, setStateFilter] = useState("ALL");
  const [queueFilter, setQueueFilter] = useState("ALL");
  const [page, setPage] = useState(1);
  const pageSize = 10;
  const normalizedSearch = search.trim().toLowerCase();
  const snapshotSymbolById = useMemo(
    () => new Map(snapshots.map((snapshot) => [snapshot.snapshot_id, snapshot.symbol])),
    [snapshots],
  );
  const journalStates = useMemo(
    () => Array.from(new Set(recommendations.map((recommendation) => recommendation.state))).sort(),
    [recommendations],
  );

  useEffect(() => {
    setPage(1);
  }, [activeTab, search, decisionFilter, qualityFilter, stateFilter, queueFilter, selectedProfile]);

  const filteredDecisions = useMemo(() => decisions.filter((decision) => {
    if (decision.profile !== selectedProfile) return false;
    if (decisionFilter !== "ALL" && decision.decision !== decisionFilter) return false;
    const symbol = snapshotSymbolById.get(decision.snapshot_id) ?? "";
    return !normalizedSearch || [symbol, decision.snapshot_id, decision.summary_vi, decision.decision]
      .some((value) => value.toLowerCase().includes(normalizedSearch));
  }), [decisions, decisionFilter, normalizedSearch, selectedProfile, snapshotSymbolById]);

  const filteredSnapshots = useMemo(() => snapshots.filter((snapshot) => {
    if (qualityFilter !== "ALL" && snapshot.quality_status !== qualityFilter) return false;
    return !normalizedSearch || [snapshot.symbol, snapshot.snapshot_id, snapshotFeatureSummary(snapshot)]
      .some((value) => value.toLowerCase().includes(normalizedSearch));
  }), [normalizedSearch, qualityFilter, snapshots]);

  const filteredJournal = useMemo(() => recommendations.filter((recommendation) => {
    if (recommendation.profile !== selectedProfile) return false;
    if (stateFilter !== "ALL" && recommendation.state !== stateFilter) return false;
    return !normalizedSearch || [recommendation.symbol ?? "", recommendation.recommendation_id, recommendation.state]
      .some((value) => value.toLowerCase().includes(normalizedSearch));
  }), [normalizedSearch, recommendations, selectedProfile, stateFilter]);

  const filteredQueue = useMemo(() => lastEvaluation
    .filter((item) => {
      const profileDecisions = item.decisions.filter((decision) => decision.profile === selectedProfile);
      const hasCandidate = profileDecisions.some((decision) => decision.candidate);
      if (queueFilter === "CANDIDATE" && !hasCandidate) return false;
      if (queueFilter === "NO_CANDIDATE" && hasCandidate) return false;
      return !normalizedSearch || [item.symbol, item.snapshot_id, ...profileDecisions.map((decision) => decision.summary_vi)]
        .some((value) => value.toLowerCase().includes(normalizedSearch));
    }), [lastEvaluation, normalizedSearch, queueFilter, selectedProfile]);

  const activeRows = activeTab === "decisions"
    ? filteredDecisions
    : activeTab === "snapshots"
      ? filteredSnapshots
      : activeTab === "journal"
        ? filteredJournal
        : filteredQueue;
  const pageCount = Math.max(1, Math.ceil(activeRows.length / pageSize));
  const pageRows = activeRows.slice((page - 1) * pageSize, page * pageSize);
  const hasFilters = Boolean(normalizedSearch) || decisionFilter !== "ALL" || qualityFilter !== "ALL" || stateFilter !== "ALL" || queueFilter !== "ALL";
  const emptyTitle = isLoading
    ? "Đang tải dữ liệu..."
    : connectionError
      ? "Chưa thể tải lịch sử"
      : activeTab === "journal"
        ? "Chưa có paper recommendation"
        : "Chưa có bản ghi phù hợp";
  const emptyDescription = connectionError
    ?? (activeTab === "journal"
      ? "Các lần NO_TRADE không tạo paper journal. Hãy xem tab Quyết định hoặc Queue audit để kiểm tra các lần quét này."
      : hasFilters
        ? "Hãy thử đổi bộ lọc hoặc từ khóa tìm kiếm."
        : "Dữ liệu sẽ xuất hiện sau lần đánh giá đầu tiên.");

  const clearFilters = () => {
    setSearch("");
    setDecisionFilter("ALL");
    setQualityFilter("ALL");
    setStateFilter("ALL");
    setQueueFilter("ALL");
  };

  return (
    <section className="history-workspace" aria-labelledby="history-heading">
      <div className="section-heading history-heading">
        <div>
          <p className="eyebrow">Historical workspace</p>
          <h2 id="history-heading">Lịch sử kiểm chứng</h2>
          <p className="section-subtitle">Lọc, tìm kiếm và xem lại dữ liệu đã lưu theo từng tầng Trade Brain.</p>
        </div>
        <span className="history-filter-count">{activeRows.length} / {activeTab === "decisions" ? decisions.length : activeTab === "snapshots" ? snapshots.length : activeTab === "journal" ? recommendations.length : lastEvaluation.length} bản ghi</span>
      </div>

      <div className="history-tabs" role="tablist" aria-label="Loại lịch sử">
        {historyTabLabels.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`history-tab ${activeTab === tab.id ? "is-active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <strong>{tab.label}</strong>
            <small>{tab.description}</small>
          </button>
        ))}
      </div>

      <div className="history-toolbar">
        <label className="history-search">
          <span>Tìm trong lịch sử</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Mã giao dịch, snapshot, nội dung..."
            type="search"
          />
        </label>
        {activeTab === "decisions" ? (
          <label className="history-filter">
            <span>Quyết định</span>
            <select value={decisionFilter} onChange={(event) => setDecisionFilter(event.target.value)}>
              <option value="ALL">Tất cả</option>
              <option value="LONG">LONG</option>
              <option value="SHORT">SHORT</option>
              <option value="WAIT">WAIT</option>
              <option value="NO_TRADE">NO TRADE</option>
            </select>
          </label>
        ) : null}
        {activeTab === "snapshots" ? (
          <label className="history-filter">
            <span>Chất lượng</span>
            <select value={qualityFilter} onChange={(event) => setQualityFilter(event.target.value)}>
              <option value="ALL">Tất cả</option>
              <option value="VALID">VALID</option>
              <option value="DEGRADED">DEGRADED</option>
              <option value="AMBIGUOUS">AMBIGUOUS</option>
              <option value="INVALID">INVALID</option>
            </select>
          </label>
        ) : null}
        {activeTab === "journal" ? (
          <label className="history-filter">
            <span>Trạng thái</span>
            <select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}>
              <option value="ALL">Tất cả</option>
              {journalStates.map((state) => <option value={state} key={state}>{state.replaceAll("_", " ")}</option>)}
            </select>
          </label>
        ) : null}
        {activeTab === "queue" ? (
          <label className="history-filter">
            <span>Ứng viên</span>
            <select value={queueFilter} onChange={(event) => setQueueFilter(event.target.value)}>
              <option value="ALL">Tất cả</option>
              <option value="CANDIDATE">Có ứng viên</option>
              <option value="NO_CANDIDATE">Không có ứng viên</option>
            </select>
          </label>
        ) : null}
        {hasFilters ? <button type="button" className="symbol-action history-clear" onClick={clearFilters}>Xóa lọc</button> : null}
      </div>

      <div className="history-table-wrap">
        {pageRows.length === 0 ? (
          <div className="history-empty">
            <strong>{emptyTitle}</strong>
            <span>{emptyDescription}</span>
          </div>
        ) : (
          <table className="history-table">
            <thead>
              {activeTab === "decisions" ? <tr><th>Thời gian</th><th>Mã</th><th>Tầng</th><th>Quyết định</th><th>Kiểm tra</th><th>Tóm tắt</th></tr> : null}
              {activeTab === "snapshots" ? <tr><th>Thời gian</th><th>Mã</th><th>Chế độ</th><th>Chất lượng</th><th>Features</th></tr> : null}
              {activeTab === "journal" ? <tr><th>Thời gian</th><th>Mã</th><th>Tầng</th><th>Side</th><th>Trạng thái</th><th className="numeric">P&amp;L</th></tr> : null}
              {activeTab === "queue" ? <tr><th>Mã</th><th>Snapshot</th><th>Cổng máy</th><th>Quyết định</th><th>Lý do</th></tr> : null}
            </thead>
            <tbody>
              {activeTab === "decisions" ? (pageRows as DecisionRecord[]).map((decision) => (
                <tr key={decision.decision_id}>
                  <td>{decision.created_at ? formatSnapshotTime(decision.created_at) : "Chưa ghi thời gian"}</td>
                  <td><strong>{snapshotSymbolById.get(decision.snapshot_id) ?? "Chưa xác định"}</strong><small>{decision.snapshot_id}</small></td>
                  <td>{profileLabel(decision.profile)}</td>
                  <td><StatusBadge value={decision.decision} /></td>
                  <td><StatusBadge value={decision.validation_status} /></td>
                  <td>{decision.summary_vi}<small>{decision.selected_candidate_id ? `Candidate ${decision.selected_candidate_id}` : "Không chọn candidate"}</small></td>
                </tr>
              )) : null}
              {activeTab === "snapshots" ? (pageRows as Snapshot[]).map((snapshot) => (
                <tr key={snapshot.snapshot_id}>
                  <td>{formatSnapshotTime(snapshot.decision_time)}</td>
                  <td><strong>{snapshot.symbol}</strong><small>{snapshot.snapshot_id}</small></td>
                  <td>{snapshot.data_mode}</td>
                  <td><StatusBadge value={snapshot.quality_status} /><small>{Object.keys(snapshot.features ?? {}).length} biến</small></td>
                  <td>{snapshotFeatureSummary(snapshot)}</td>
                </tr>
              )) : null}
              {activeTab === "journal" ? (pageRows as PaperRecommendation[]).map((recommendation) => (
                <tr key={recommendation.recommendation_id}>
                  <td>{formatSnapshotTime(recommendation.emitted_at)}</td>
                  <td><strong>{recommendation.symbol ?? "Chưa xác định"}</strong><small>{recommendation.recommendation_id}</small></td>
                  <td>{profileLabel(recommendation.profile)}</td>
                  <td><StatusBadge value={recommendation.side} /></td>
                  <td><StatusBadge value={recommendation.state} /><small>Data {recommendation.data_quality}</small></td>
                  <td className="numeric">{recommendation.net_pnl ?? "Chưa đóng"}</td>
                </tr>
              )) : null}
              {activeTab === "queue" ? (pageRows as EvaluationSummaryItem[]).map((item) => {
                const profileDecisions = item.decisions.filter((decision) => decision.profile === selectedProfile);
                return (
                  <tr key={item.snapshot_id}>
                    <td><strong>{item.symbol}</strong></td>
                    <td><small>{item.snapshot_id}</small></td>
                    <td>{item.eligible_count}/{item.candidate_count}</td>
                    <td>{profileDecisions.length > 0 ? profileDecisions.map((decision) => <StatusBadge value={decision.decision} key={`${item.snapshot_id}-${decision.profile}`} />) : "Chưa có"}</td>
                    <td>{profileDecisions.map((decision) => decision.summary_vi).join(" · ") || "Chưa có quyết định"}</td>
                  </tr>
                );
              }) : null}
            </tbody>
          </table>
        )}
      </div>

      <div className="history-footer">
        <span>Đang xem {pageRows.length} bản ghi, tầng {profileLabel(selectedProfile)}</span>
        <PaginationControls page={Math.min(page, pageCount)} pageCount={pageCount} onChange={setPage} />
      </div>
    </section>
  );
}

export default function DashboardPage() {
  const [selectedProfile, setSelectedProfile] = useState<Profile>("BALANCED");
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [recommendations, setRecommendations] = useState<PaperRecommendation[]>([]);
  const [accounts, setAccounts] = useState<PaperAccount[]>([]);
  const [reports, setReports] = useState<Array<PaperReport | null>>([]);
  const [completedTradeReports, setCompletedTradeReports] = useState<Array<PaperReport | null>>([]);
  const [globalSetupReport, setGlobalSetupReport] = useState<GlobalReport | null>(null);
  const [evaluationStatus, setEvaluationStatus] = useState<EvaluationStatus | null>(null);
  const [lastEvaluation, setLastEvaluation] = useState<EvaluationSummaryItem[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [evaluationMessage, setEvaluationMessage] = useState<string | null>(null);
  const activeProfile = profiles.find((profile) => profile.id === selectedProfile) ?? profiles[1];

  const loadSnapshots = useCallback(async () => {
    setIsLoading(true);
    setConnectionError(null);
    try {
      const [healthResponse, decisionResponse, snapshotResponse, paperResponse, reportResponse, accountResponse, evaluationStatusResponse] = await Promise.all([
        fetch("/api/health", { cache: "no-store" }),
        fetch("/api/decisions/recent?limit=100", { cache: "no-store" }),
        fetch("/api/snapshots?limit=100", { cache: "no-store" }),
        fetch("/api/paper/recommendations", { cache: "no-store" }),
        fetch("/api/reports", { cache: "no-store" }),
        fetch("/api/paper/accounts", { cache: "no-store" }),
        fetch("/api/evaluation/status", { cache: "no-store" }),
      ]);
      const healthPayload: unknown = await healthResponse.json();
      const decisionPayload: unknown = await decisionResponse.json();
      const snapshotPayload: unknown = await snapshotResponse.json();
      const paperPayload: unknown = await paperResponse.json();
      const reportPayload: unknown = await reportResponse.json();
      const accountPayload: unknown = await accountResponse.json();
      const evaluationStatusPayload: unknown = await evaluationStatusResponse.json();
      if (!healthResponse.ok || !isHealthResponse(healthPayload)) {
        throw new Error("Trade Brain chưa phản hồi trạng thái PAPER hợp lệ.");
      }
      if (!decisionResponse.ok || !isDecisionResponse(decisionPayload)) {
        throw new Error("Decision history chưa phản hồi đúng định dạng.");
      }
      if (!snapshotResponse.ok || !isSnapshotResponse(snapshotPayload)) {
        throw new Error("Trade Brain chưa phản hồi đúng định dạng.");
      }
      if (!paperResponse.ok || !isPaperResponse(paperPayload)) {
        throw new Error("Paper journal chưa phản hồi đúng định dạng.");
      }
      if (!reportResponse.ok || !isReportResponse(reportPayload)) {
        throw new Error("Report chưa phản hồi đúng định dạng.");
      }
      if (!accountResponse.ok || !isAccountResponse(accountPayload)) {
        throw new Error("Paper accounts chưa phản hồi đúng định dạng.");
      }
      setHealth(healthPayload);
      setDecisions(decisionPayload.decisions);
      setSnapshots(snapshotPayload.snapshots);
      setRecommendations(paperPayload.recommendations);
      setReports(reportPayload.reports);
      setCompletedTradeReports(reportPayload.completed_trade_reports);
      setGlobalSetupReport(reportPayload.global_setup_report);
      setAccounts(accountPayload.accounts);
      if (evaluationStatusResponse.ok && isEvaluationStatusResponse(evaluationStatusPayload)) {
        setEvaluationStatus(evaluationStatusPayload.evaluation);
        setLastEvaluation(evaluationStatusPayload.evaluation.result_summary ?? []);
      }
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : "Không thể tải dữ liệu.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const activeReport = reports.find((report) => report?.profile === selectedProfile) ?? null;
  const activeCompletedReport = completedTradeReports.find((report) => report?.profile === selectedProfile) ?? null;
  const activeRecommendations = recommendations.filter(
    (recommendation) => recommendation.profile === selectedProfile,
  );
  const dashboardMetrics = [
    {
      label: "Tỷ lệ lời ròng",
      value: formatRatio(activeReport?.net_positive_rate, "Chưa đủ 100 mẫu"),
      helper: activeReport?.cohort_status ?? `Cohort ${activeProfile.label} chưa khóa`,
    },
    {
      label: "Net P&L",
      value: activeReport?.total_net_pnl ?? "Chưa đủ dữ liệu",
      helper: "Paper result sau chi phí",
    },
    {
      label: "Sụt vốn lớn nhất",
      value: formatRatio(activeReport?.max_drawdown, "Chưa đủ dữ liệu"),
      helper: `Cohort ${activeProfile.label}`,
    },
    {
      label: "Mốc báo cáo",
      value: `${activeReport?.recommendation_count ?? 0} / 100`,
      helper: `${activeCompletedReport?.closed_count ?? 0} trade đã đóng xác định`,
    },
  ];

  const runEvaluation = useCallback(async () => {
    setIsEvaluating(true);
    setEvaluationMessage(null);
    try {
      const response = await fetch("/api/evaluate", { method: "POST" });
      const payload: unknown = await response.json();
      if (!response.ok || !payload || typeof payload !== "object" || !("message" in payload)) {
        const errorPayload = payload as { error?: unknown; detail?: unknown } | null;
        const message = typeof errorPayload?.error === "string"
          ? errorPayload.error
          : typeof errorPayload?.detail === "string"
            ? errorPayload.detail
            : "Không thể chạy đánh giá.";
        throw new Error(message);
      }
      const result = payload as EvaluationResponse;
      setEvaluationMessage(result.message);
      if (result.result_summary) setLastEvaluation(result.result_summary);
      await loadSnapshots();
    } catch (error) {
      setEvaluationMessage(error instanceof Error ? error.message : "Không thể chạy đánh giá.");
    } finally {
      setIsEvaluating(false);
    }
  }, [loadSnapshots]);

  useEffect(() => {
    void loadSnapshots();
  }, [loadSnapshots]);

  useEffect(() => {
    const refreshTimer = window.setInterval(() => {
      void fetch("/api/evaluation/status", { cache: "no-store" })
        .then((response) => response.json() as Promise<unknown>)
        .then((payload) => {
          if (isEvaluationStatusResponse(payload)) {
            setEvaluationStatus(payload.evaluation);
            setLastEvaluation(payload.evaluation.result_summary ?? []);
          }
        })
        .catch(() => undefined);
    }, 2_000);
    return () => window.clearInterval(refreshTimer);
  }, []);

  const isQueueRunning = evaluationStatus?.status === "RUNNING";
  useEffect(() => {
    const refreshTimer = window.setInterval(() => {
      void loadSnapshots();
    }, 15_000);

    return () => window.clearInterval(refreshTimer);
  }, [loadSnapshots]);

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Trade V2 trang chính">
          <span className="brand-mark" aria-hidden="true">TV</span>
          <span>
            <strong>Trade V2</strong>
            <small>Paper Lab</small>
          </span>
        </a>
        <div className="topbar-actions">
          <span className="mode-badge"><span className="status-dot" /> PAPER MODE</span>
          <button
            className="primary-button"
            type="button"
            onClick={() => void runEvaluation()}
            disabled={isEvaluating || isLoading}
            aria-busy={isEvaluating}
          >
            {isEvaluating ? "Đang đánh giá..." : "Đánh giá ngay"}
          </button>
          <span className={`queue-status ${isQueueRunning ? "is-running" : ""}`} role="status" aria-live="polite">
            {isQueueRunning
              ? `🔄 Đang quét ${evaluationStatus?.current_index ?? 0}/${evaluationStatus?.total_count ?? 0}: ${evaluationStatus?.current_symbol ?? "đang chuẩn bị"}`
              : evaluationStatus?.status === "FAILED"
                ? `Queue lỗi: ${evaluationStatus.error ?? "không rõ nguyên nhân"}`
                : "Queue sẵn sàng"}
          </span>
          <button className="ghost-button" type="button" onClick={() => void loadSnapshots()} disabled={isLoading}>
            {isLoading ? "Đang tải..." : "Đồng bộ dữ liệu"}
          </button>
          <a className="ghost-button config-link" href="/config">Cấu hình Trade Brain</a>
          <span className={`action-feedback ${evaluationMessage ? "is-visible" : ""}`} role="status" aria-live="polite">
            {evaluationMessage ?? ""}
          </span>
        </div>
      </header>

      <section className="intro" id="top">
        <div>
          <p className="eyebrow">Bộ não Trade V2 / Tổng quan</p>
          <h1>Kiểm chứng trước khi xuống tiền.</h1>
          <p className="intro-copy">
            Theo dõi quyết định của Claude, kết quả mô phỏng và bằng chứng thống kê trong cùng một nơi.
          </p>
        </div>
        <div className="health-card" aria-label="Trạng thái hệ thống">
          <span className="health-label">Hệ thống</span>
          <strong>
            {connectionError ? "Chưa kết nối Trade Brain" : health?.real_money_enabled === false ? "Chỉ PAPER" : "Đang kiểm tra"}
          </strong>
          <span>{connectionError ?? "Không có đường đặt lệnh thật. Snapshot chỉ đọc và paper mode."}</span>
        </div>
      </section>

      <section className="profile-section" aria-labelledby="profile-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Ba chính sách độc lập</p>
            <h2 id="profile-heading">Chọn tầng để xem</h2>
          </div>
          <span className="selected-label">Đang xem: {activeProfile.label}</span>
        </div>
        <div className="profile-tabs" role="tablist" aria-label="Tầng khẩu vị">
          {profiles.map((profile) => (
            <button
              className={`profile-tab ${profile.id === selectedProfile ? "is-active" : ""}`}
              key={profile.id}
              type="button"
              role="tab"
              aria-selected={profile.id === selectedProfile}
              onClick={() => {
                setSelectedProfile(profile.id);
              }}
            >
              <span>{profile.label}</span>
              <small>{profile.description}</small>
            </button>
          ))}
        </div>
      </section>

      <section className="metrics-grid" aria-label="Chỉ số PAPER">
        {dashboardMetrics.map((metric) => (
          <article className="metric-card" key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
            <small>{metric.helper}</small>
          </article>
        ))}
      </section>

      <section className="metrics-grid report-context" aria-label="Ngữ cảnh báo cáo">
        <article className="metric-card">
          <span>Cohort {activeProfile.label}</span>
          <strong>{activeReport?.recommendation_count ?? 0} đề xuất</strong>
          <small>{activeReport?.cohort_status ?? "Chưa khóa cohort"}</small>
        </article>
        <article className="metric-card">
          <span>Kết quả đã đóng</span>
          <strong>{activeCompletedReport?.closed_count ?? 0} trade</strong>
          <small>Chỉ tính lệnh có kết quả xác định</small>
        </article>
        <article className="metric-card">
          <span>Setup chung</span>
          <strong>{globalSetupReport?.recommendation_count ?? 0} setup</strong>
          <small>Đếm setup_id khác nhau giữa các tầng</small>
        </article>
      </section>

      <HistoryWorkspace
        selectedProfile={selectedProfile}
        decisions={decisions}
        snapshots={snapshots}
        recommendations={recommendations}
        lastEvaluation={lastEvaluation}
        isLoading={isLoading}
        connectionError={connectionError}
      />

      <section className="snapshot-panel" aria-labelledby="accounts-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Independent paper ledgers</p>
            <h2 id="accounts-heading">Ba sổ vốn PAPER</h2>
          </div>
          <span className="selected-label">{accounts.length} profiles</span>
        </div>
        <div className="snapshot-list">
          {accounts.map((account) => (
              <article className="snapshot-row" key={account.profile}>
                <strong>{account.profile}</strong>
                <span>Equity {account.mark_to_market_equity} USDT</span>
                <span>P&L {account.realized_pnl} USDT</span>
            </article>
          ))}
        </div>
      </section>

      <section className="empty-panel" aria-labelledby="empty-heading">
        <div className="empty-icon" aria-hidden="true">01</div>
        <div>
          <p className="eyebrow">Chưa có khuyến nghị</p>
          <h2 id="empty-heading">Bắt đầu từ nền dữ liệu sạch</h2>
          <p>
            Khi worker lấy được snapshot đầu tiên, trang này sẽ hiển thị candidate, quyết định, giá mô phỏng,
            chi phí và trạng thái của tầng {activeProfile.label}.
          </p>
          <div className="empty-checklist" aria-label="Các bước cần hoàn tất">
            <span><b>1</b> Kết nối Binance public data</span>
            <span><b>2</b> Lưu snapshot vào Supabase</span>
            <span><b>3</b> Chạy paper recommendation</span>
          </div>
        </div>
      </section>
    </main>
  );
}
