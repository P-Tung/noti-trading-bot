"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Profile = "PROACTIVE" | "BALANCED" | "CAUTIOUS";
type PaperMode = "RESEARCH_PAPER" | "VERIFIED_PAPER";
type UniverseMode = "BINANCE_VOLUME" | "MANUAL";
type BrainVersion = "config-v1" | "config-v2" | "custom";

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
  symbols: string[];
  universe_mode: UniverseMode;
  universe: UniverseConfig;
  initial_equity_usdt: number;
  t1: StrategyConfig;
  t2: StrategyConfig;
  r1: StrategyConfig;
  profiles: Record<Profile, ProfilePolicy>;
};

type SavedConfig = {
  config_id: string;
  name: string;
  config_version: string;
  created_at?: string;
  updated_at?: string;
};

const profileLabels: Record<Profile, string> = {
  PROACTIVE: "Chủ động",
  BALANCED: "Cân bằng",
  CAUTIOUS: "Thận trọng",
};

const strategyLabels: Record<keyof Pick<TradeBrainConfig, "t1" | "t2" | "r1">, string> = {
  t1: "T1 · Vượt mốc rồi kiểm tra lại",
  t2: "T2 · Điều chỉnh rồi đi tiếp",
  r1: "R1 · Bật lại trong vùng dao động",
};

const strategyFieldLabels: Record<string, string> = {
  pivot_left: "Số nến bên trái để tìm mốc giá",
  pivot_right: "Số nến bên phải để xác nhận mốc giá",
  breakout_buffer_atr: "Khoảng vượt mốc tối thiểu",
  min_break_body_ratio: "Thân nến tối thiểu khi vượt mốc",
  min_break_volume_ratio: "Khối lượng tối thiểu khi vượt mốc",
  retest_window_bars: "Số nến chờ kiểm tra lại",
  opposite_penetration_atr: "Mức xuyên ngược cho phép",
  stop_buffer_atr: "Khoảng đệm cho điểm dừng lỗ",
  target_r: "Mức chốt lời theo mức rủi ro",
  ema_period: "Số nến dùng để tính xu hướng",
  atr_period: "Số nến dùng để tính biên độ giá",
  pullback_tolerance_atr: "Mức điều chỉnh cho phép",
  horizon_bars: "Số nến theo dõi kết quả",
  range_bars: "Số nến dùng để xác định vùng giá",
  sweep_buffer_atr: "Khoảng vượt biên cho phép",
};

const brainVersionOptions: Array<{ value: BrainVersion; label: string; description: string }> = [
  { value: "config-v1", label: "Trade Brain V1", description: "Bộ cài đặt nền tảng cũ" },
  { value: "config-v2", label: "Trade Brain V2", description: "Bộ cài đặt hiện tại" },
  { value: "custom", label: "Cấu hình custom", description: "Bộ cài đặt do bạn tự điều chỉnh" },
];

function strategyFieldLabel(key: string): string {
  return strategyFieldLabels[key] ?? key.replaceAll("_", " ");
}

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

function getBrainVersion(configVersion: string): BrainVersion {
  if (configVersion === "config-v1") return "config-v1";
  if (configVersion === "config-v2") return "config-v2";
  return "custom";
}

export default function TradeBrainConfigPage() {
  const [config, setConfig] = useState<TradeBrainConfig | null>(null);
  const [status, setStatus] = useState("Đang tải cấu hình...");
  const [isSaving, setIsSaving] = useState(false);
  const [binanceSymbols, setBinanceSymbols] = useState<BinanceSymbol[]>([]);
  const [symbolQuery, setSymbolQuery] = useState("");
  const [symbolsStatus, setSymbolsStatus] = useState("Đang tải danh sách Binance...");
  const [isSymbolDropdownOpen, setIsSymbolDropdownOpen] = useState(false);
  const [savedConfigs, setSavedConfigs] = useState<SavedConfig[]>([]);
  const [selectedSavedConfigId, setSelectedSavedConfigId] = useState("");
  const [savedConfigName, setSavedConfigName] = useState("V2 hiện tại");
  const [isLoadingSavedConfig, setIsLoadingSavedConfig] = useState(false);
  const [selectedBrainVersion, setSelectedBrainVersion] = useState<BrainVersion>("config-v2");

  useEffect(() => {
    fetch("/api/config", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json() as { config?: TradeBrainConfig; error?: string };
        if (!response.ok || !payload.config) throw new Error(payload.error ?? "Không thể tải cấu hình.");
        setConfig(payload.config);
        setSelectedBrainVersion(getBrainVersion(payload.config.config_version));
        setStatus("Đã tải cài đặt hiện tại từ máy chủ");
      })
      .catch((error: unknown) => setStatus(error instanceof Error ? error.message : "Không thể tải cấu hình."));
  }, []);

  useEffect(() => {
    fetch("/api/config/saved", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json() as { configs?: SavedConfig[]; error?: string };
        if (!response.ok || !payload.configs) throw new Error(payload.error ?? "Không thể tải cấu hình đã lưu.");
        setSavedConfigs(payload.configs);
      })
      .catch((error: unknown) => setStatus(error instanceof Error ? error.message : "Không thể tải cấu hình đã lưu."));
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

  function selectBrainVersion(version: BrainVersion) {
    if (!config) return;
    setSelectedBrainVersion(version);
    setSelectedSavedConfigId("");
    setConfig({ ...config, config_version: version === "custom" ? "custom" : version });
    setSavedConfigName(version === "config-v1" ? "V1 nền tảng cũ" : version === "config-v2" ? "V2 hiện tại" : "Cấu hình custom mới");
    setStatus(version === "custom" ? "Đã tạo bản nháp custom mới. Bạn có thể chỉnh sửa rồi lưu." : `Đã chọn ${version === "config-v1" ? "Trade Brain V1" : "Trade Brain V2"}. Bấm lưu để áp dụng.`);
  }

  function createCustomConfig() {
    selectBrainVersion("custom");
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
      const savedResponse = await fetch("/api/config/saved", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: savedConfigName.trim() || "V2 hiện tại", config: payload.config }),
      });
      const savedPayload = await savedResponse.json() as { saved?: SavedConfig; error?: string; detail?: string };
      if (!savedResponse.ok || !savedPayload.saved) throw new Error(savedPayload.error ?? savedPayload.detail ?? "Đã cập nhật active nhưng chưa lưu được bản đặt tên.");
      setSavedConfigs((current) => [savedPayload.saved!, ...current.filter((item) => item.config_id !== savedPayload.saved!.config_id)]);
      setSelectedSavedConfigId(savedPayload.saved.config_id);
      setStatus("Đã lưu và áp dụng cài đặt, đồng thời tạo một bản có tên.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Không thể lưu cấu hình.");
    } finally {
      setIsSaving(false);
    }
  }

  async function loadSavedConfig(configId: string) {
    if (!configId) return;
    setSelectedSavedConfigId(configId);
    setIsLoadingSavedConfig(true);
    setStatus("Đang mở bản cài đặt đã lưu...");
    try {
      const response = await fetch(`/api/config/saved/${encodeURIComponent(configId)}`, { cache: "no-store" });
      const payload = await response.json() as { config?: TradeBrainConfig; error?: string; detail?: string };
      if (!response.ok || !payload.config) throw new Error(payload.error ?? payload.detail ?? "Không thể nạp bản cấu hình.");
      setConfig(payload.config);
      setSelectedBrainVersion(getBrainVersion(payload.config.config_version));
      setStatus("Đã mở bản cài đặt. Bấm “Lưu và áp dụng cài đặt” để sử dụng.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Không thể nạp bản cấu hình.");
    } finally {
      setIsLoadingSavedConfig(false);
    }
  }

  if (!config) return <main className="shell"><a className="back-link" href="/">← Về dashboard</a><p className="config-status">{status}</p></main>;

  return (
    <main className="shell config-shell">
      <a className="back-link" href="/">← Về trang tổng quan</a>
      <header className="config-header">
        <div>
          <p className="eyebrow">TRADE BRAIN V2 / CÀI ĐẶT</p>
          <h1>Cài đặt cách đánh giá</h1>
          <p className="intro-copy">Chọn mã giao dịch và điều chỉnh cách hệ thống tìm tín hiệu. Mọi thay đổi chỉ dùng để mô phỏng, không đặt lệnh thật.</p>
        </div>
        <span className="mode-badge"><span className="status-dot" /> CHỈ MÔ PHỎNG</span>
      </header>

      <form onSubmit={saveConfig}>
        <section className="config-section">
          <div className="section-heading"><div><p className="eyebrow">DỮ LIỆU ĐẦU VÀO</p><h2>Mã giao dịch và cách lấy dữ liệu</h2></div></div>
          <div className="config-grid config-grid-wide">
            <div className="config-field config-version-field">
              <span>Chọn Trade Brain</span>
              <div className="config-version-control">
                <select value={selectedBrainVersion} onChange={(event) => selectBrainVersion(event.target.value as BrainVersion)} aria-label="Chọn phiên bản Trade Brain">
                  {brainVersionOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                <button className="ghost-button custom-config-button" type="button" onClick={createCustomConfig}>+ Tạo custom</button>
              </div>
              <small className="field-helper">{brainVersionOptions.find((option) => option.value === selectedBrainVersion)?.description}</small>
            </div>
            <label className="config-field"><span>Mở cài đặt đã lưu</span><select value={selectedSavedConfigId} onChange={(event) => loadSavedConfig(event.target.value)} disabled={isLoadingSavedConfig}>
              <option value="">Chọn một bản cài đặt...</option>
              {savedConfigs.map((savedConfig) => <option key={savedConfig.config_id} value={savedConfig.config_id}>{savedConfig.name} · {savedConfig.config_version}</option>)}
            </select></label>
            <label className="config-field"><span>Tên bản cài đặt mới</span><input value={savedConfigName} maxLength={80} onChange={(event) => setSavedConfigName(event.target.value)} placeholder="Ví dụ: 5 mã giao dịch phổ biến" /></label>
            <label className="config-field"><span>Cách chọn mã giao dịch</span><select value={config.universe_mode} onChange={(event) => setConfig({ ...config, universe_mode: event.target.value as UniverseMode })}><option value="BINANCE_VOLUME">Tự lọc mã có giao dịch nhiều trên Binance</option><option value="MANUAL">Tự chọn từ danh sách Binance</option></select></label>
            <NumberField label="Giá trị giao dịch tối thiểu trong 24 giờ (USDT)" value={config.universe.min_quote_volume_24h_usdt} step="1000000" min="1" onChange={(value) => setConfig({ ...config, universe: { ...config.universe, min_quote_volume_24h_usdt: value } })} />
            <NumberField label="Khoảng thời gian làm mới danh sách (giây)" value={config.universe.refresh_seconds} step="60" min="60" onChange={(value) => setConfig({ ...config, universe: { ...config.universe, refresh_seconds: value } })} />
            <NumberField label="Thời gian danh sách được xem là còn mới (giây)" value={config.universe.max_universe_age_seconds} step="60" min="60" onChange={(value) => setConfig({ ...config, universe: { ...config.universe, max_universe_age_seconds: value } })} />
            <NumberField label="Số ngày dữ liệu cần có" value={config.universe.min_history_days} step="1" min="1" onChange={(value) => setConfig({ ...config, universe: { ...config.universe, min_history_days: value } })} />
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
              <small className="field-helper">{config.universe_mode === "BINANCE_VOLUME" ? "Hệ thống tự xem xét các hợp đồng vĩnh cửu USDT đang giao dịch và ưu tiên mã có giá trị giao dịch đủ lớn. Không giới hạn số lượng mã." : "Danh sách lấy trực tiếp từ Binance và chỉ gồm các mã đang được giao dịch."}</small>
            </div>
            <label className="config-field"><span>Chế độ mô phỏng</span><select value={config.paper_mode} onChange={(event) => setConfig({ ...config, paper_mode: event.target.value as PaperMode })}><option value="RESEARCH_PAPER">Nghiên cứu mô phỏng</option><option value="VERIFIED_PAPER">Mô phỏng đã xác minh</option></select></label>
            <NumberField label="Số vốn mô phỏng (USDT)" value={config.initial_equity_usdt} step="1" min="1" onChange={(value) => setConfig({ ...config, initial_equity_usdt: value })} />
          </div>
          <p className="config-warning">Chỉ chọn “Mô phỏng đã xác minh” khi đã có đủ dữ liệu kiểm chứng ngoài mẫu.</p>
        </section>

        {(Object.keys(strategyLabels) as Array<"t1" | "t2" | "r1">).map((strategy) => (
          <section className="config-section" key={strategy}>
            <div className="section-heading"><div><p className="eyebrow">CÁCH TÌM TÍN HIỆU</p><h2>{strategyLabels[strategy]}</h2></div><span className="selected-label">Các con số dùng để nhận diện tín hiệu</span></div>
            <div className="config-grid">
              {Object.entries(config[strategy]).map(([key, value]) => (
                <NumberField key={key} label={strategyFieldLabel(key)} value={value} onChange={(nextValue) => setConfig(updateStrategy(config, strategy, key, nextValue))} />
              ))}
            </div>
          </section>
        ))}

        <section className="config-section">
          <div className="section-heading"><div><p className="eyebrow">GIỚI HẠN AN TOÀN</p><h2>Ba cách đánh giá và giới hạn rủi ro</h2></div></div>
          <div className="profile-config-grid">
            {(Object.keys(profileLabels) as Profile[]).map((profile) => {
              const policy = config.profiles[profile];
              return <article className="profile-config-card" key={profile}><h3>{profileLabels[profile]}</h3><div className="config-grid">
                <NumberField label="Điểm chất lượng tối thiểu" value={policy.quality_minimum} step="1" onChange={(value) => updateProfile(profile, "quality_minimum", value)} />
                <NumberField label="Lợi nhuận kỳ vọng tối thiểu (R)" value={policy.expectancy_minimum_r} onChange={(value) => updateProfile(profile, "expectancy_minimum_r", value)} />
                <NumberField label="Số mẫu tối thiểu để tham khảo" value={policy.effective_sample_minimum} step="1" onChange={(value) => updateProfile(profile, "effective_sample_minimum", value)} />
                <NumberField label="Mức vốn chấp nhận cho mỗi lệnh (tỷ lệ)" value={policy.risk_per_trade_pct} step="0.0005" onChange={(value) => updateProfile(profile, "risk_per_trade_pct", value)} />
                <NumberField label="Tổng vốn đang chịu rủi ro (tỷ lệ)" value={policy.max_open_risk_pct} step="0.0005" onChange={(value) => updateProfile(profile, "max_open_risk_pct", value)} />
                <NumberField label="Rủi ro tối đa trong cùng nhóm mã (tỷ lệ)" value={policy.max_cluster_risk_pct} step="0.0005" onChange={(value) => updateProfile(profile, "max_cluster_risk_pct", value)} />
                <NumberField label="Mức giảm vốn tối đa trong ngày (tỷ lệ)" value={policy.daily_drawdown_stop_pct} step="0.001" onChange={(value) => updateProfile(profile, "daily_drawdown_stop_pct", value)} />
                <NumberField label="Mức giảm vốn tối đa trong 30 ngày (tỷ lệ)" value={policy.rolling_drawdown_stop_pct} step="0.001" onChange={(value) => updateProfile(profile, "rolling_drawdown_stop_pct", value)} />
              </div></article>;
            })}
          </div>
        </section>

        <div className="config-actions"><a className="ghost-button config-cancel" href="/">Quay lại</a><button className="primary-button" type="submit" disabled={isSaving}>{isSaving ? "Đang lưu cài đặt..." : "Lưu và áp dụng cài đặt"}</button><span className="action-feedback is-visible" role="status">{status}</span></div>
      </form>
    </main>
  );
}
