"use client";

import { useCallback, useEffect, useState } from "react";

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
};

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

export default function DashboardPage() {
  const [selectedProfile, setSelectedProfile] = useState<Profile>("BALANCED");
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [recommendations, setRecommendations] = useState<PaperRecommendation[]>([]);
  const [accounts, setAccounts] = useState<PaperAccount[]>([]);
  const [reports, setReports] = useState<Array<PaperReport | null>>([]);
  const [completedTradeReports, setCompletedTradeReports] = useState<Array<PaperReport | null>>([]);
  const [globalSetupReport, setGlobalSetupReport] = useState<GlobalReport | null>(null);
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
      const [healthResponse, decisionResponse, snapshotResponse, paperResponse, reportResponse, accountResponse] = await Promise.all([
        fetch("/api/health", { cache: "no-store" }),
        fetch("/api/decisions/recent?limit=20", { cache: "no-store" }),
        fetch("/api/snapshots?limit=20", { cache: "no-store" }),
        fetch("/api/paper/recommendations", { cache: "no-store" }),
        fetch("/api/reports", { cache: "no-store" }),
        fetch("/api/paper/accounts", { cache: "no-store" }),
      ]);
      const healthPayload: unknown = await healthResponse.json();
      const decisionPayload: unknown = await decisionResponse.json();
      const snapshotPayload: unknown = await snapshotResponse.json();
      const paperPayload: unknown = await paperResponse.json();
      const reportPayload: unknown = await reportResponse.json();
      const accountPayload: unknown = await accountResponse.json();
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
      void loadSnapshots();
    }, 15_000);

    return () => window.clearInterval(refreshTimer);
  }, [loadSnapshots]);

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Trade V1 trang chính">
          <span className="brand-mark" aria-hidden="true">TV</span>
          <span>
            <strong>Trade V1</strong>
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
          <p className="eyebrow">Bộ não Trade V1 / Tổng quan</p>
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
              onClick={() => setSelectedProfile(profile.id)}
            >
              <span>{profile.label}</span>
              <small>{profile.description}</small>
            </button>
          ))}
        </div>
      </section>

      <section className="snapshot-panel" aria-labelledby="decision-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Claude decision audit</p>
            <h2 id="decision-heading">Quyết định gần nhất của ba tầng</h2>
          </div>
          <span className="selected-label">{decisions.length} quyết định</span>
        </div>
        {decisions.length > 0 ? (
          <div className="snapshot-list">
            {decisions.slice(0, 6).map((decision) => (
              <article className="snapshot-row" key={decision.decision_id}>
                <strong>{profiles.find((profile) => profile.id === decision.profile)?.label ?? decision.profile}</strong>
                <span>{decision.decision} · {decision.validation_status}</span>
                <small>
                  {decision.summary_vi}
                  {decision.selected_candidate_id ? ` · Candidate ${decision.selected_candidate_id}` : ""}
                </small>
                {decision.candidate ? (
                  <small>
                    {decision.candidate.strategy} {decision.candidate.entry_stage} · Entry {decision.candidate.entry_estimate}
                    · SL {decision.candidate.stop_price} · TP {decision.candidate.target_price}
                    {decision.risk?.quantity_allowed !== undefined
                      ? ` · Qty ${decision.risk.quantity_allowed}`
                      : ""}
                  </small>
                ) : null}
                {decision.created_at ? (
                  <time dateTime={decision.created_at}>{formatSnapshotTime(decision.created_at)}</time>
                ) : (
                  <time dateTime={decision.snapshot_id}>Snapshot {decision.snapshot_id}</time>
                )}
              </article>
            ))}
          </div>
        ) : (
          <p className="panel-note">Chưa có decision audit. Hãy chạy decision worker sau khi có snapshot.</p>
        )}
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

      <section className="snapshot-panel" aria-labelledby="snapshot-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Market snapshots</p>
            <h2 id="snapshot-heading">Dữ liệu gần nhất</h2>
          </div>
          <span className="selected-label">{snapshots.length} snapshot</span>
        </div>
        {snapshots.length > 0 ? (
          <div className="snapshot-list">
            {snapshots.slice(0, 5).map((snapshot) => (
              <article className="snapshot-row" key={snapshot.snapshot_id}>
                <strong>{snapshot.symbol}</strong>
                <span>{snapshot.data_mode} · {snapshotQualityLabel(snapshot)}</span>
                <small>{snapshotFeatureSummary(snapshot)}</small>
                <time dateTime={snapshot.decision_time}>{formatSnapshotTime(snapshot.decision_time)}</time>
              </article>
            ))}
          </div>
        ) : (
          <p className="panel-note">
            {isLoading ? "Đang tải snapshot..." : "Chưa có snapshot. Hãy khởi động Trade Brain collector."}
          </p>
        )}
      </section>

      <section className="snapshot-panel" aria-labelledby="journal-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Paper journal</p>
            <h2 id="journal-heading">Recommendation gần nhất</h2>
          </div>
          <span className="selected-label">{activeRecommendations.length} {activeProfile.label}</span>
        </div>
        {activeRecommendations.length > 0 ? (
          <div className="snapshot-list">
            {activeRecommendations.slice(0, 5).map((recommendation) => (
              <article className="snapshot-row" key={recommendation.recommendation_id}>
                <strong>{recommendation.symbol ?? "Mã chưa xác định"} · {recommendation.side}</strong>
                <span>
                  {recommendation.state} · Data {recommendation.data_quality} · Qty {recommendation.quantity}
                  {recommendation.entry_fill !== null ? ` · Entry ${recommendation.entry_fill}` : ""}
                  {recommendation.exit_fill !== null ? ` · Exit ${recommendation.exit_fill}` : ""}
                  {recommendation.funding_pnl !== "0" ? ` · Funding ${recommendation.funding_pnl}` : ""}
                  {recommendation.net_pnl !== null ? ` · P&L ${recommendation.net_pnl}` : ""}
                </span>
                <time dateTime={recommendation.emitted_at}>{formatSnapshotTime(recommendation.emitted_at)}</time>
              </article>
            ))}
          </div>
        ) : (
          <p className="panel-note">Chưa có paper recommendation.</p>
        )}
      </section>

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
