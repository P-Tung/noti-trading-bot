"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Profile = "PROACTIVE" | "BALANCED" | "CAUTIOUS";

const profiles: Array<{ id: Profile; label: string; description: string }> = [
  { id: "PROACTIVE", label: "Chủ động", description: "Ưu tiên cơ hội sớm, chấp nhận chờ xác nhận thêm" },
  { id: "BALANCED", label: "Cân bằng", description: "Cân bằng giữa cơ hội và độ chắc chắn" },
  { id: "CAUTIOUS", label: "Thận trọng", description: "Chỉ chọn tín hiệu rõ, ưu tiên hạn chế rủi ro" },
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
  return `${qualityLabel(snapshot.quality_status)} · ${featureCount} thông số`;
}

const decisionLabels: Record<string, string> = {
  LONG: "Có thể mua",
  SHORT: "Có thể bán",
  WAIT: "Chờ thêm tín hiệu",
  NO_TRADE: "Không giao dịch",
};

const qualityLabels: Record<string, string> = {
  VALID: "Dữ liệu đủ tốt",
  DEGRADED: "Dữ liệu còn thiếu",
  AMBIGUOUS: "Dữ liệu chưa rõ",
  INVALID: "Dữ liệu không hợp lệ",
};

const statusLabels: Record<string, string> = {
  RECOMMENDED: "Được đề xuất",
  OPEN: "Đang mở",
  CLOSED_TP: "Đã chốt lời",
  CLOSED_SL: "Đã dừng lỗ",
  TIMEOUT: "Hết thời gian theo dõi",
  VALID: "Đã kiểm tra",
  INVALID: "Không hợp lệ",
  SERVICE_ERROR: "Lỗi dịch vụ",
};

const dataModeLabels: Record<string, string> = {
  PRICE_ONLY: "Chỉ dữ liệu giá",
  MIXED: "Dữ liệu giá và giao dịch",
};

function qualityLabel(value: string): string {
  return qualityLabels[value.toUpperCase()] ?? value.replaceAll("_", " ");
}

function statusLabel(value: string): string {
  const normalized = value.toUpperCase();
  return decisionLabels[normalized] ?? qualityLabels[normalized] ?? statusLabels[normalized] ?? value.replaceAll("_", " ");
}

function dataModeLabel(value: string): string {
  return dataModeLabels[value.toUpperCase()] ?? value.replaceAll("_", " ");
}

function snapshotFeatureSummary(snapshot: Snapshot): string {
  const features = snapshot.features ?? {};
  const summary: string[] = [];
  const close = features.close_15m?.value;
  const atr = features.atr_15m?.value;
  const structure = features.structure_state_15m?.value;
  const spread = features.spread_bps?.value;
  const rangePosition = features.range_position_15m?.value;
  if (typeof close === "number") summary.push(`Giá ${close}`);
  if (typeof atr === "number") summary.push(`Biên độ ${atr}`);
  if (typeof structure === "string") summary.push(`Cấu trúc ${structure}`);
  if (typeof spread === "number") summary.push(`Chênh lệch ${spread.toFixed(1)} điểm cơ bản`);
  if (typeof rangePosition === "number") summary.push(`Vị trí trong vùng ${(rangePosition * 100).toFixed(0)}%`);
  return summary.join(" · ") || "Chưa có thông số tóm tắt";
}

function formatRatio(value: string | null | undefined, emptyLabel: string): string {
  if (value === null || value === undefined) return emptyLabel;
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? `${(numericValue * 100).toFixed(1)}%` : emptyLabel;
}

function PaginationControls({
  page,
  pageCount,
  pageSize,
  totalRows,
  onChange,
  onPageSizeChange,
}: {
  page: number;
  pageCount: number;
  pageSize: number;
  totalRows: number;
  onChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const firstRow = totalRows === 0 ? 0 : (page - 1) * pageSize + 1;
  const lastRow = Math.min(page * pageSize, totalRows);
  return (
    <div className="pagination" aria-label="Phân trang dữ liệu">
      <label className="page-size-control">
        <span>Hiển thị</span>
        <select value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
          {[10, 25, 50].map((size) => <option value={size} key={size}>{size} dòng</option>)}
        </select>
      </label>
      <span className="pagination-range">{firstRow}-{lastRow} / {totalRows}</span>
      <button type="button" className="pagination-icon-button" disabled={page === 1 || pageCount === 0} onClick={() => onChange(1)} aria-label="Về trang đầu" title="Về trang đầu"><span aria-hidden="true">«</span></button>
      <button type="button" className="pagination-icon-button" disabled={page === 1 || pageCount === 0} onClick={() => onChange(page - 1)} aria-label="Về trang trước" title="Về trang trước"><span aria-hidden="true">‹</span></button>
      <span>Trang {page} / {Math.max(pageCount, 1)}</span>
      <button type="button" className="pagination-icon-button" disabled={page === pageCount || pageCount === 0} onClick={() => onChange(page + 1)} aria-label="Về trang sau" title="Về trang sau"><span aria-hidden="true">›</span></button>
      <button type="button" className="pagination-icon-button" disabled={page === pageCount || pageCount === 0} onClick={() => onChange(pageCount)} aria-label="Đến trang cuối" title="Đến trang cuối"><span aria-hidden="true">»</span></button>
    </div>
  );
}

type HistoryTab = "decisions" | "snapshots" | "journal" | "queue";

const historyTabLabels: Array<{ id: HistoryTab; label: string; description: string }> = [
  { id: "decisions", label: "Nhận định", description: "Kết luận của AI" },
  { id: "snapshots", label: "Dữ liệu thị trường", description: "Thông số lúc quét" },
  { id: "journal", label: "Lệnh mô phỏng", description: "Kết quả PAPER" },
  { id: "queue", label: "Lần đánh giá gần nhất", description: "Tiến độ từng mã" },
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
  return <span className={`history-badge ${statusTone(value)}`}>{statusLabel(value)}</span>;
}

function QueueProgressPanel({ evaluationStatus }: { evaluationStatus: EvaluationStatus | null }) {
  const isRunning = evaluationStatus?.status === "RUNNING";
  const completedCount = evaluationStatus?.completed_count ?? 0;
  const totalCount = evaluationStatus?.total_count ?? 0;
  const progress = totalCount > 0 ? Math.min(100, Math.round((completedCount / totalCount) * 100)) : 0;
  const title = isRunning
    ? "Đang quét dữ liệu"
    : evaluationStatus?.status === "FAILED"
      ? "Lần đánh giá bị gián đoạn"
      : evaluationStatus?.status === "COMPLETED"
        ? "Lần đánh giá đã hoàn tất"
        : "Trạng thái lần đánh giá";
  const description = isRunning
    ? `Hệ thống đang kiểm tra mã ${evaluationStatus?.current_symbol ?? "chuẩn bị bắt đầu"}. Bạn có thể theo dõi tiến độ bên dưới.`
    : evaluationStatus?.status === "FAILED"
      ? evaluationStatus.error ?? "Không rõ nguyên nhân. Hãy thử đánh giá lại."
      : evaluationStatus?.status === "COMPLETED"
        ? `Đã kiểm tra xong ${completedCount} mã trong lần đánh giá gần nhất.`
        : "Chưa có lần đánh giá nào đang chạy.";

  return (
    <section className={`queue-progress-panel ${isRunning ? "is-running" : ""}`} aria-live="polite" aria-labelledby="queue-progress-heading">
      <div className="queue-progress-icon" aria-hidden="true">{isRunning ? "↻" : "✓"}</div>
      <div className="queue-progress-body">
        <div className="queue-progress-heading">
          <div>
            <p className="eyebrow">TIẾN ĐỘ ĐÁNH GIÁ</p>
            <h3 id="queue-progress-heading">{title}</h3>
          </div>
          <strong>{completedCount}/{totalCount || "-"} mã</strong>
        </div>
        <div className="queue-progress-track" aria-label={`Đã hoàn thành ${completedCount} trên ${totalCount} mã`}>
          <span style={{ width: `${progress}%` }} />
        </div>
        <p className="queue-progress-description">{description}</p>
        {isRunning ? <span className="queue-progress-current">Mã đang xử lý: <strong>{evaluationStatus?.current_symbol ?? "Đang chuẩn bị"}</strong></span> : null}
      </div>
    </section>
  );
}

function HistoryWorkspace({
  selectedProfile,
  decisions,
  snapshots,
  recommendations,
  lastEvaluation,
  evaluationStatus,
  isLoading,
  connectionError,
}: {
  selectedProfile: Profile;
  decisions: DecisionRecord[];
  snapshots: Snapshot[];
  recommendations: PaperRecommendation[];
  lastEvaluation: EvaluationSummaryItem[];
  evaluationStatus: EvaluationStatus | null;
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
  const [pageSize, setPageSize] = useState(10);
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
  }, [activeTab, search, decisionFilter, qualityFilter, stateFilter, queueFilter, selectedProfile, pageSize]);

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
  const pageCount = Math.ceil(activeRows.length / pageSize);
  const pageRows = activeRows.slice((page - 1) * pageSize, page * pageSize);
  const hasFilters = Boolean(normalizedSearch) || decisionFilter !== "ALL" || qualityFilter !== "ALL" || stateFilter !== "ALL" || queueFilter !== "ALL";
  const emptyTitle = isLoading
    ? "Đang tải dữ liệu..."
    : connectionError
      ? "Chưa thể tải lịch sử"
      : activeTab === "journal"
        ? "Chưa có lệnh mô phỏng"
        : "Chưa có bản ghi phù hợp";
  const emptyDescription = connectionError
    ?? (isLoading
      ? "Đang lấy dữ liệu lịch sử từ Trade Brain..."
      : activeTab === "journal"
      ? "Các mã không có tín hiệu sẽ không tạo lệnh mô phỏng. Hãy xem tab Nhận định để biết lý do."
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
          <p className="eyebrow">LỊCH SỬ ĐÁNH GIÁ</p>
          <h2 id="history-heading">Xem lại các lần đánh giá</h2>
          <p className="section-subtitle">Tìm lại dữ liệu thị trường, nhận định của AI và kết quả mô phỏng.</p>
        </div>
        <span className="history-filter-count">
          {isLoading
            ? "Đang tải bản ghi..."
            : `${activeRows.length} / ${activeTab === "decisions" ? decisions.length : activeTab === "snapshots" ? snapshots.length : activeTab === "journal" ? recommendations.length : lastEvaluation.length} bản ghi phù hợp`}
        </span>
      </div>

      <QueueProgressPanel evaluationStatus={evaluationStatus} />

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
          <span>Tìm kiếm</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Tìm theo mã, thời gian hoặc nội dung..."
            type="search"
          />
        </label>
        {activeTab === "decisions" ? (
          <label className="history-filter">
            <span>Kết luận</span>
            <select value={decisionFilter} onChange={(event) => setDecisionFilter(event.target.value)}>
              <option value="ALL">Tất cả</option>
              <option value="LONG">Có thể mua</option>
              <option value="SHORT">Có thể bán</option>
              <option value="WAIT">Chờ thêm tín hiệu</option>
              <option value="NO_TRADE">Không giao dịch</option>
            </select>
          </label>
        ) : null}
        {activeTab === "snapshots" ? (
          <label className="history-filter">
            <span>Chất lượng dữ liệu</span>
            <select value={qualityFilter} onChange={(event) => setQualityFilter(event.target.value)}>
              <option value="ALL">Tất cả</option>
              <option value="VALID">Dữ liệu đủ tốt</option>
              <option value="DEGRADED">Dữ liệu còn thiếu</option>
              <option value="AMBIGUOUS">Dữ liệu chưa rõ</option>
              <option value="INVALID">Dữ liệu không hợp lệ</option>
            </select>
          </label>
        ) : null}
        {activeTab === "journal" ? (
          <label className="history-filter">
            <span>Kết quả mô phỏng</span>
            <select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}>
              <option value="ALL">Tất cả</option>
              {journalStates.map((state) => <option value={state} key={state}>{statusLabel(state)}</option>)}
            </select>
          </label>
        ) : null}
        {activeTab === "queue" ? (
          <label className="history-filter">
            <span>Tín hiệu</span>
            <select value={queueFilter} onChange={(event) => setQueueFilter(event.target.value)}>
              <option value="ALL">Tất cả</option>
              <option value="CANDIDATE">Có tín hiệu</option>
              <option value="NO_CANDIDATE">Chưa có tín hiệu</option>
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
              {activeTab === "decisions" ? <tr><th>Thời gian</th><th>Mã</th><th>Cách đánh giá</th><th>Kết luận</th><th>Kiểm tra dữ liệu</th><th>Lý do</th></tr> : null}
              {activeTab === "snapshots" ? <tr><th>Thời gian</th><th>Mã</th><th>Loại dữ liệu</th><th>Chất lượng</th><th>Thông số đo được</th></tr> : null}
              {activeTab === "journal" ? <tr><th>Thời gian</th><th>Mã</th><th>Cách đánh giá</th><th>Hướng</th><th>Trạng thái</th><th className="numeric">Lãi/lỗ</th></tr> : null}
              {activeTab === "queue" ? <tr><th>Mã</th><th>Mã dữ liệu</th><th>Điều kiện đạt</th><th>Kết luận</th><th>Lý do</th></tr> : null}
            </thead>
            <tbody>
              {activeTab === "decisions" ? (pageRows as DecisionRecord[]).map((decision) => (
                <tr key={decision.decision_id}>
                  <td>{decision.created_at ? formatSnapshotTime(decision.created_at) : "Chưa ghi thời gian"}</td>
                  <td><strong>{snapshotSymbolById.get(decision.snapshot_id) ?? "Chưa xác định"}</strong><small>{decision.snapshot_id}</small></td>
                  <td>{profileLabel(decision.profile)}</td>
                  <td><StatusBadge value={decision.decision} /></td>
                  <td><StatusBadge value={decision.validation_status} /></td>
                  <td>{decision.summary_vi}<small>{decision.selected_candidate_id ? `Tín hiệu ${decision.selected_candidate_id}` : "Không có tín hiệu phù hợp"}</small></td>
                </tr>
              )) : null}
              {activeTab === "snapshots" ? (pageRows as Snapshot[]).map((snapshot) => (
                <tr key={snapshot.snapshot_id}>
                  <td>{formatSnapshotTime(snapshot.decision_time)}</td>
                  <td><strong>{snapshot.symbol}</strong><small>{snapshot.snapshot_id}</small></td>
                  <td>{dataModeLabel(snapshot.data_mode)}</td>
                  <td><StatusBadge value={snapshot.quality_status} /><small>{Object.keys(snapshot.features ?? {}).length} thông số</small></td>
                  <td>{snapshotFeatureSummary(snapshot)}</td>
                </tr>
              )) : null}
              {activeTab === "journal" ? (pageRows as PaperRecommendation[]).map((recommendation) => (
                <tr key={recommendation.recommendation_id}>
                  <td>{formatSnapshotTime(recommendation.emitted_at)}</td>
                  <td><strong>{recommendation.symbol ?? "Chưa xác định"}</strong><small>{recommendation.recommendation_id}</small></td>
                  <td>{profileLabel(recommendation.profile)}</td>
                  <td><StatusBadge value={recommendation.side} /></td>
                  <td><StatusBadge value={recommendation.state} /><small>Dữ liệu: {qualityLabel(recommendation.data_quality)}</small></td>
                  <td className="numeric">{recommendation.net_pnl ?? "Đang mở"}</td>
                </tr>
              )) : null}
              {activeTab === "queue" ? (pageRows as EvaluationSummaryItem[]).map((item) => {
                const profileDecisions = item.decisions.filter((decision) => decision.profile === selectedProfile);
                return (
                  <tr key={item.snapshot_id}>
                    <td><strong>{item.symbol}</strong></td>
                    <td><small>{item.snapshot_id}</small></td>
                    <td>{item.eligible_count}/{item.candidate_count}</td>
                    <td>{profileDecisions.length > 0 ? profileDecisions.map((decision) => <StatusBadge value={decision.decision} key={`${item.snapshot_id}-${decision.profile}`} />) : "Chưa có kết luận"}</td>
                    <td>{profileDecisions.map((decision) => decision.summary_vi).join(" · ") || "Chưa có quyết định"}</td>
                  </tr>
                );
              }) : null}
            </tbody>
          </table>
        )}
      </div>

      <div className="history-footer">
        <span>{isLoading ? "Đang tải dữ liệu lịch sử..." : `Đang xem ${pageRows.length} dòng trong ${activeRows.length} bản ghi, cách đánh giá ${profileLabel(selectedProfile)}`}</span>
        <PaginationControls
          page={Math.min(page, Math.max(pageCount, 1))}
          pageCount={pageCount}
          pageSize={pageSize}
          totalRows={activeRows.length}
          onChange={setPage}
          onPageSizeChange={(nextPageSize) => {
            setPageSize(nextPageSize);
            setPage(1);
          }}
        />
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
      label: "Tỷ lệ kết quả dương",
      value: formatRatio(activeReport?.net_positive_rate, "Chưa đủ 100 mẫu"),
      helper: activeReport?.cohort_status ?? `Chưa đủ mẫu của cách ${activeProfile.label.toLowerCase()}`,
    },
    {
      label: "Lãi/lỗ mô phỏng",
      value: activeReport?.total_net_pnl ?? "Chưa đủ dữ liệu",
      helper: "Sau phí mô phỏng",
    },
    {
      label: "Mức giảm vốn lớn nhất",
      value: formatRatio(activeReport?.max_drawdown, "Chưa đủ dữ liệu"),
      helper: `Cách đánh giá ${activeProfile.label}`,
    },
    {
      label: "Tiến độ báo cáo",
      value: `${activeReport?.recommendation_count ?? 0} / 100`,
      helper: `${activeCompletedReport?.closed_count ?? 0} lệnh đã có kết quả`,
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
          <span className="mode-badge"><span className="status-dot" /> CHỈ MÔ PHỎNG</span>
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
                ? `Lần đánh giá lỗi: ${evaluationStatus.error ?? "không rõ nguyên nhân"}`
                : "Sẵn sàng đánh giá"}
          </span>
          <button className="ghost-button" type="button" onClick={() => void loadSnapshots()} disabled={isLoading}>
            {isLoading ? "Đang tải..." : "Tải lại dữ liệu"}
          </button>
          <a className="ghost-button config-link" href="/config">Cài đặt cách đánh giá</a>
          <span className={`action-feedback ${evaluationMessage ? "is-visible" : ""}`} role="status" aria-live="polite">
            {evaluationMessage ?? ""}
          </span>
        </div>
      </header>

      <section className="intro" id="top">
        <div>
          <p className="eyebrow">TRADE BRAIN V2 / TỔNG QUAN</p>
          <h1>Hiểu tín hiệu trước khi quyết định.</h1>
          <p className="intro-copy">
            Xem tín hiệu mua hoặc bán, kết quả mô phỏng và lý do hệ thống đưa ra từng nhận định.
          </p>
        </div>
        <div className="health-card" aria-label="Trạng thái hệ thống">
          <span className="health-label">Hệ thống</span>
          <strong>
            {connectionError ? "Chưa kết nối hệ thống" : health?.real_money_enabled === false ? "Chỉ mô phỏng, chưa đặt lệnh thật" : "Đang kiểm tra"}
          </strong>
          <span>{connectionError ?? "Dữ liệu chỉ dùng để phân tích. Hệ thống không tự mua hoặc bán bằng tiền thật."}</span>
        </div>
      </section>

      <section className="profile-section" aria-labelledby="profile-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">BA CÁCH ĐÁNH GIÁ TÍN HIỆU</p>
            <h2 id="profile-heading">Chọn cách hệ thống đánh giá cơ hội</h2>
          </div>
          <span className="selected-label">Đang xem: {activeProfile.label}</span>
        </div>
        <div className="profile-tabs" role="tablist" aria-label="Cách đánh giá tín hiệu">
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

      <section className="metrics-grid" aria-label="Kết quả mô phỏng">
        {dashboardMetrics.map((metric) => (
          <article className="metric-card" key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
            <small>{metric.helper}</small>
          </article>
        ))}
      </section>

      <section className="metrics-grid report-context" aria-label="Thông tin kết quả">
        <article className="metric-card">
          <span>Kết quả của cách {activeProfile.label.toLowerCase()}</span>
          <strong>{activeReport?.recommendation_count ?? 0} đề xuất</strong>
          <small>{activeReport?.cohort_status ?? "Chưa đủ mẫu để kết luận"}</small>
        </article>
        <article className="metric-card">
          <span>Kết quả đã đóng</span>
          <strong>{activeCompletedReport?.closed_count ?? 0} lệnh</strong>
          <small>Chỉ tính lệnh mô phỏng đã có kết quả</small>
        </article>
        <article className="metric-card">
          <span>Tín hiệu chung</span>
          <strong>{globalSetupReport?.recommendation_count ?? 0} tín hiệu</strong>
          <small>Không đếm trùng giữa các cách đánh giá</small>
        </article>
      </section>

      <HistoryWorkspace
        selectedProfile={selectedProfile}
        decisions={decisions}
        snapshots={snapshots}
        recommendations={recommendations}
        lastEvaluation={lastEvaluation}
        evaluationStatus={evaluationStatus}
        isLoading={isLoading}
        connectionError={connectionError}
      />

      <section className="snapshot-panel" aria-labelledby="accounts-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">TÀI KHOẢN MÔ PHỎNG ĐỘC LẬP</p>
            <h2 id="accounts-heading">Ba tài khoản mô phỏng</h2>
          </div>
          <span className="selected-label">{accounts.length} cách đánh giá</span>
        </div>
        <div className="snapshot-list">
          {accounts.map((account) => (
              <article className="snapshot-row" key={account.profile}>
                <strong>{profileLabel(account.profile)}</strong>
                <span>Số dư mô phỏng {account.mark_to_market_equity} USDT</span>
                <span>Lãi/lỗ {account.realized_pnl} USDT</span>
            </article>
          ))}
        </div>
      </section>

      <section className="empty-panel" aria-labelledby="empty-heading">
        <div className="empty-icon" aria-hidden="true">01</div>
        <div>
          <p className="eyebrow">CHƯA CÓ KẾT QUẢ ĐÁNH GIÁ</p>
          <h2 id="empty-heading">Bắt đầu bằng một lần đánh giá</h2>
          <p>
            Bấm “Đánh giá ngay” để lấy dữ liệu mới nhất, nhận diện tín hiệu và xem kết quả mô phỏng của cách {activeProfile.label.toLowerCase()}.
          </p>
          <div className="empty-checklist" aria-label="Các bước cần hoàn tất">
            <span><b>1</b> Nhận dữ liệu công khai từ Binance</span>
            <span><b>2</b> Lưu dữ liệu vào lịch sử</span>
            <span><b>3</b> Tạo nhận định mô phỏng</span>
          </div>
        </div>
      </section>
    </main>
  );
}
