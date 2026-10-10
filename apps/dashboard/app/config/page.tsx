"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Profile = "PROACTIVE" | "BALANCED" | "CAUTIOUS";
type PaperMode = "RESEARCH_PAPER" | "VERIFIED_PAPER";
type UniverseMode = "BINANCE_VOLUME" | "MANUAL";

type StrategyConfig = Record<string, number>;
type BinanceSymbol = { symbol: string; baseAsset: string; quoteAsset: string; contractType: string };

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

type UniverseConfig = {
  min_quote_volume_24h_usdt: number;
  refresh_seconds: number;
  max_universe_age_seconds: number;
  max_symbols: number | null;
  min_history_days: number;
  require_strategy_indicator_warmup: boolean;
};

type TradeBrainConfig = {
  config_version: string;
  paper_mode: PaperMode;
  automatic_evaluation_enabled: boolean;
  symbols: string[];
  universe_mode: UniverseMode;
  universe: UniverseConfig;
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
  const [binanceSymbols, setBinanceSymbols] = useState<BinanceSymbol[]>([]);
  const [symbolQuery, setSymbolQuery] = useState("");
  const [symbolsStatus, setSymbolsStatus] = useState("Đang tải danh sách Binance...");
  const [isSymbolDropdownOpen, setIsSymbolDropdownOpen] = useState(false);

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

  useEffect(() => {
    fetch("/api/binance/symbols", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json() as { symbols?: BinanceSymbol[]; error?: string };
        if (!response.ok || !payload.symbols) throw new Error(payload.error ?? "Không thể tải danh sách Binance.");
        setBinanceSymbols(payload.symbols);
        setSymbolsStatus(`${payload.symbols.length} hợp đồng vĩnh cửu USDT đang giao dịch`);
      })
      .catch((error: unknown) => setSymbolsStatus(error instanceof Error ? error.message : "Không thể tải danh sách Binance."));
  }, []);

  function updateProfile(profile: Profile, key: keyof ProfilePolicy, value: number) {
    if (!config) return;
    setConfig({ ...config, profiles: { ...config.profiles, [profile]: { ...config.profiles[profile], [key]: value } } });
  }

  function toggleSymbol(symbol: string) {
    if (!config) return;
    const isSelected = config.symbols.includes(symbol);
    setConfig({ ...config, symbols: isSelected ? config.symbols.filter((item) => item !== symbol) : [...config.symbols, symbol] });
  }

  const filteredSymbols = useMemo(
    () => binanceSymbols.filter((item) => item.symbol.includes(symbolQuery) || item.baseAsset.includes(symbolQuery)),
    [binanceSymbols, symbolQuery],
  );

  function selectAllFilteredSymbols() {
    if (!config) return;
    const availableSymbols = filteredSymbols.filter((item) => !config.symbols.includes(item.symbol));
    setConfig({ ...config, symbols: [...config.symbols, ...availableSymbols.map((item) => item.symbol)] });
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

  async function toggleAutomaticEvaluation() {
    if (!config || isSaving) return;
    const nextConfig = {
      ...config,
      automatic_evaluation_enabled: !config.automatic_evaluation_enabled,
    };
    setIsSaving(true);
    setStatus(nextConfig.automatic_evaluation_enabled ? "Đang bật đánh giá tự động..." : "Đang tắt đánh giá tự động...");
    try {
      const response = await fetch("/api/config", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(nextConfig),
      });
      const payload = await response.json() as { config?: TradeBrainConfig; error?: string; detail?: string };
      if (!response.ok || !payload.config) throw new Error(payload.error ?? payload.detail ?? "Không thể cập nhật chế độ tự động.");
      setConfig(payload.config);
      setStatus(payload.config.automatic_evaluation_enabled ? "Đã bật đánh giá tự động." : "Đã tắt đánh giá tự động.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Không thể cập nhật chế độ tự động.");
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
          <p className="intro-copy">Điều chỉnh các tham số nghiên cứu theo bộ não V2. Tất cả thay đổi vẫn nằm trong PAPER mode.</p>
        </div>
        <span className="mode-badge"><span className="status-dot" /> PAPER ONLY</span>
      </header>

      <form onSubmit={saveConfig}>
        <section className="config-section">
          <div className="section-heading"><div><p className="eyebrow">Experiment</p><h2>Chế độ và dữ liệu</h2></div></div>
          <div className="config-grid config-grid-wide">
            <label className="config-field"><span>Phiên bản cấu hình</span><input value={config.config_version} onChange={(event) => setConfig({ ...config, config_version: event.target.value })} /></label>
            <label className="config-field"><span>Nguồn universe V2</span><select value={config.universe_mode} onChange={(event) => setConfig({ ...config, universe_mode: event.target.value as UniverseMode })}><option value="BINANCE_VOLUME">Binance tự lọc volume ≥ 20 triệu USDT</option><option value="MANUAL">Tự chọn mã thủ công</option></select></label>
            <NumberField label="Volume tối thiểu 24h, USDT" value={config.universe.min_quote_volume_24h_usdt} step="1000000" min="1" onChange={(value) => setConfig({ ...config, universe: { ...config.universe, min_quote_volume_24h_usdt: value } })} />
            <NumberField label="Refresh universe, giây" value={config.universe.refresh_seconds} step="60" min="60" onChange={(value) => setConfig({ ...config, universe: { ...config.universe, refresh_seconds: value } })} />
            <NumberField label="Tuổi tối đa universe, giây" value={config.universe.max_universe_age_seconds} step="60" min="60" onChange={(value) => setConfig({ ...config, universe: { ...config.universe, max_universe_age_seconds: value } })} />
            <NumberField label="Lịch sử tối thiểu, ngày" value={config.universe.min_history_days} step="1" min="1" onChange={(value) => setConfig({ ...config, universe: { ...config.universe, min_history_days: value } })} />
            <div className="symbol-picker config-field">
              <span>{config.universe_mode === "BINANCE_VOLUME" ? "Mã tham khảo từ Binance" : "Mã giao dịch, chọn từ Binance"}</span>
              <div className="symbol-dropdown">
                <button className="symbol-dropdown-trigger" type="button" aria-expanded={isSymbolDropdownOpen} onClick={() => setIsSymbolDropdownOpen(!isSymbolDropdownOpen)}>
                  <span>{config.symbols.length ? `${config.symbols.length} mã đã chọn` : "Chọn mã giao dịch"}</span>
                  <span aria-hidden="true">{isSymbolDropdownOpen ? "⌃" : "⌄"}</span>
                </button>
                {isSymbolDropdownOpen ? <div className="symbol-dropdown-menu">
                  <div className="symbol-picker-toolbar">
                    <input aria-label="Tìm mã Binance" placeholder="Tìm BTC, ETH..." value={symbolQuery} onChange={(event) => setSymbolQuery(event.target.value.toUpperCase())} />
                    <span>{config.symbols.length} đã chọn</span>
                  </div>
                  <div className="symbol-dropdown-actions">
                    <button type="button" className="symbol-action" onClick={selectAllFilteredSymbols}>Chọn tất cả</button>
                    <button type="button" className="symbol-action" onClick={() => setConfig({ ...config, symbols: [] })}>Bỏ chọn tất cả</button>
                  </div>
                  <div className="selected-symbols" aria-label="Mã đã chọn">{config.symbols.map((symbol) => <button type="button" key={symbol} className="selected-symbol" onClick={() => toggleSymbol(symbol)}>{symbol} ×</button>)}</div>
                  <div className="symbol-list" role="group" aria-label="Danh sách mã Binance">
                    {filteredSymbols.slice(0, 80).map((item) => <label className="symbol-option" key={item.symbol}><input type="checkbox" checked={config.symbols.includes(item.symbol)} onChange={() => toggleSymbol(item.symbol)} /><span>{item.symbol}</span><small>{item.baseAsset}/USDT</small></label>)}
                    {binanceSymbols.length === 0 ? <p className="panel-note">{symbolsStatus}</p> : null}
                  </div>
                </div> : null}
              </div>
              <small className="field-helper">{config.universe_mode === "BINANCE_VOLUME" ? "V2 sẽ tự quét toàn bộ hợp đồng vĩnh cửu USDT TRADING, giữ mã có volume 24h từ 20 triệu USDT, không giới hạn 30 mã." : "Nguồn: Binance USDⓈ-M perpetual, chỉ lấy mã đang ở trạng thái TRADING."}</small>
            </div>
            <label className="config-field"><span>Chế độ PAPER</span><select value={config.paper_mode} onChange={(event) => setConfig({ ...config, paper_mode: event.target.value as PaperMode })}><option value="RESEARCH_PAPER">RESEARCH_PAPER</option><option value="VERIFIED_PAPER">VERIFIED_PAPER</option></select></label>
            <div className="config-field">
              <span>Đánh giá tự động</span>
              <button className="ghost-button" type="button" onClick={() => void toggleAutomaticEvaluation()} disabled={isSaving} aria-pressed={config.automatic_evaluation_enabled}>
                {config.automatic_evaluation_enabled ? "Đang bật" : "Đang tắt"}
              </button>
              <small className="field-helper">Mặc định tắt. Chỉ bật khi muốn worker tự quét theo chu kỳ và gọi Claude.</small>
            </div>
            <NumberField label="Vốn mô phỏng, USDT" value={config.initial_equity_usdt} step="1" min="1" onChange={(value) => setConfig({ ...config, initial_equity_usdt: value })} />
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
