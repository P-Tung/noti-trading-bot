"use client";

import { FormEvent, useEffect, useState } from "react";

type Profile = "PROACTIVE" | "BALANCED" | "CAUTIOUS";
type PaperMode = "RESEARCH_PAPER" | "VERIFIED_PAPER";

type StrategyConfig = Record<string, number>;

type ProfilePolicy = {
  quality_minimum: number;
  expectancy_minimum_r: number;
  effective_sample_minimum: number;
  risk_per_trade_pct: number;
  max_open_risk_pct: number;
  max_cluster_risk_pct: number;
  daily_drawdown_stop_pct: number;
  rolling_drawdown_stop_pct: number;
};

type TradeBrainConfig = {
  config_version: string;
  paper_mode: PaperMode;
  symbols: string[];
  initial_equity_usdt: number;
  t1: StrategyConfig;
  t2: StrategyConfig;
  r1: StrategyConfig;
  profiles: Record<Profile, ProfilePolicy>;
};

const profileLabels: Record<Profile, string> = {
  PROACTIVE: "Chủ động",
  BALANCED: "Cân bằng",
  CAUTIOUS: "Thận trọng",
};

const strategyLabels: Record<keyof Pick<TradeBrainConfig, "t1" | "t2" | "r1">, string> = {
  t1: "T1 · Phá vỡ và kiểm tra lại",
  t2: "T2 · Hồi trong xu hướng",
  r1: "R1 · Quét biên đi ngang",
};

function NumberField({
  label,
  value,
  step = "0.01",
  min = "0",
  onChange,
}: {
  label: string;
  value: number;
  step?: string;
  min?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="config-field">
      <span>{label}</span>
      <input
        type="number"
        min={min}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function updateStrategy(
  config: TradeBrainConfig,
  strategy: "t1" | "t2" | "r1",
  key: string,
  value: number,
): TradeBrainConfig {
  return { ...config, [strategy]: { ...config[strategy], [key]: value } };
}

export default function TradeBrainConfigPage() {
  const [config, setConfig] = useState<TradeBrainConfig | null>(null);
  const [status, setStatus] = useState("Đang tải cấu hình...");
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    fetch("/api/config", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json() as { config?: TradeBrainConfig; error?: string };
        if (!response.ok || !payload.config) throw new Error(payload.error ?? "Không thể tải cấu hình.");
        setConfig(payload.config);
        setStatus("Cấu hình hiện tại từ Trade Brain server");
      })
      .catch((error: unknown) => setStatus(error instanceof Error ? error.message : "Không thể tải cấu hình."));
  }, []);

  function updateProfile(profile: Profile, key: keyof ProfilePolicy, value: number) {
    if (!config) return;
    setConfig({ ...config, profiles: { ...config.profiles, [profile]: { ...config.profiles[profile], [key]: value } } });
  }

  async function saveConfig(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!config) return;
    setIsSaving(true);
    setStatus("Đang lưu cấu hình...");
    try {
      const response = await fetch("/api/config", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(config),
      });
      const payload = await response.json() as { config?: TradeBrainConfig; error?: string; detail?: string };
      if (!response.ok || !payload.config) throw new Error(payload.error ?? payload.detail ?? "Không thể lưu cấu hình.");
      setConfig(payload.config);
      setStatus("Đã lưu. Chu kỳ PAPER tiếp theo sẽ dùng cấu hình mới.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Không thể lưu cấu hình.");
    } finally {
      setIsSaving(false);
    }
  }

  if (!config) return <main className="shell"><a className="back-link" href="/">← Về dashboard</a><p className="config-status">{status}</p></main>;

  return (
    <main className="shell config-shell">
      <a className="back-link" href="/">← Về dashboard</a>
      <header className="config-header">
        <div>
          <p className="eyebrow">Trade Brain / Research controls</p>
          <h1>Hiệu chỉnh Trade Brain</h1>
          <p className="intro-copy">Điều chỉnh các tham số nghiên cứu trong tài liệu V1. Tất cả thay đổi vẫn nằm trong PAPER mode.</p>
        </div>
        <span className="mode-badge"><span className="status-dot" /> PAPER ONLY</span>
      </header>

      <form onSubmit={saveConfig}>
        <section className="config-section">
          <div className="section-heading"><div><p className="eyebrow">Experiment</p><h2>Chế độ và dữ liệu</h2></div></div>
          <div className="config-grid config-grid-wide">
            <label className="config-field"><span>Phiên bản cấu hình</span><input value={config.config_version} onChange={(event) => setConfig({ ...config, config_version: event.target.value })} /></label>
            <label className="config-field"><span>Mã giao dịch, tối đa 30</span><input value={config.symbols.join(", ")} onChange={(event) => setConfig({ ...config, symbols: event.target.value.split(",").map((symbol) => symbol.trim().toUpperCase()).filter(Boolean) })} /></label>
            <label className="config-field"><span>Chế độ PAPER</span><select value={config.paper_mode} onChange={(event) => setConfig({ ...config, paper_mode: event.target.value as PaperMode })}><option value="RESEARCH_PAPER">RESEARCH_PAPER</option><option value="VERIFIED_PAPER">VERIFIED_PAPER</option></select></label>
            <NumberField label="Vốn mô phỏng, USDT" value={config.initial_equity_usdt} step="100" min="1" onChange={(value) => setConfig({ ...config, initial_equity_usdt: value })} />
          </div>
          <p className="config-warning">Không chỉnh `VERIFIED_PAPER` nếu chưa có đủ bằng chứng ngoài mẫu theo tài liệu.</p>
        </section>

        {(Object.keys(strategyLabels) as Array<"t1" | "t2" | "r1">).map((strategy) => (
          <section className="config-section" key={strategy}>
            <div className="section-heading"><div><p className="eyebrow">Strategy parameters</p><h2>{strategyLabels[strategy]}</h2></div><span className="selected-label">Tham số nghiên cứu</span></div>
            <div className="config-grid">
              {Object.entries(config[strategy]).map(([key, value]) => (
                <NumberField key={key} label={key.replaceAll("_", " ")} value={value} onChange={(nextValue) => setConfig(updateStrategy(config, strategy, key, nextValue))} />
              ))}
            </div>
          </section>
        ))}

        <section className="config-section">
          <div className="section-heading"><div><p className="eyebrow">Risk policy</p><h2>Ba tầng khẩu vị và giới hạn cứng</h2></div></div>
          <div className="profile-config-grid">
            {(Object.keys(profileLabels) as Profile[]).map((profile) => {
              const policy = config.profiles[profile];
              return <article className="profile-config-card" key={profile}><h3>{profileLabels[profile]}</h3><div className="config-grid">
                <NumberField label="Điểm chất lượng tối thiểu" value={policy.quality_minimum} step="1" onChange={(value) => updateProfile(profile, "quality_minimum", value)} />
                <NumberField label="Expectancy tối thiểu R" value={policy.expectancy_minimum_r} onChange={(value) => updateProfile(profile, "expectancy_minimum_r", value)} />
                <NumberField label="Mẫu hiệu dụng tối thiểu" value={policy.effective_sample_minimum} step="1" onChange={(value) => updateProfile(profile, "effective_sample_minimum", value)} />
                <NumberField label="Risk mỗi lệnh, % dạng 0.005" value={policy.risk_per_trade_pct} step="0.0005" onChange={(value) => updateProfile(profile, "risk_per_trade_pct", value)} />
                <NumberField label="Tổng risk mở" value={policy.max_open_risk_pct} step="0.0005" onChange={(value) => updateProfile(profile, "max_open_risk_pct", value)} />
                <NumberField label="Risk cùng cụm" value={policy.max_cluster_risk_pct} step="0.0005" onChange={(value) => updateProfile(profile, "max_cluster_risk_pct", value)} />
                <NumberField label="Dừng lỗ trong ngày" value={policy.daily_drawdown_stop_pct} step="0.001" onChange={(value) => updateProfile(profile, "daily_drawdown_stop_pct", value)} />
                <NumberField label="Dừng sụt vốn 30 ngày" value={policy.rolling_drawdown_stop_pct} step="0.001" onChange={(value) => updateProfile(profile, "rolling_drawdown_stop_pct", value)} />
              </div></article>;
            })}
          </div>
        </section>

        <div className="config-actions"><a className="ghost-button config-cancel" href="/">Hủy</a><button className="primary-button" type="submit" disabled={isSaving}>{isSaving ? "Đang lưu..." : "Lưu cấu hình"}</button><span className="action-feedback is-visible" role="status">{status}</span></div>
      </form>
    </main>
  );
}
