"""計算技術指標與綜合「市場溫度分數」，並產出前端要用的多週期彙總 JSON。

分數設計（AS-IS，維持連續公式，未採用門檻觸發版本）：
  - RSI-14（動能，天生 0-100）
  - 200 日均線乖離率：換算成標準差單位（z-score，供顯示用）以及近 3 年百分位排名（供合成分數用）
  - 近 3 年價格百分位排名（統計性便宜／昂貴）
  - VIX 恐慌指數近 3 年百分位排名，因高 VIX＝恐慌＝相對低檔，分數方向要反轉（100－百分位）
  四者等權重平均成 0-100 綜合分數；分數愈高＝愈偏熱／相對高檔，分數愈低＝愈偏冷／相對低檔。

  扣款權重倍數：以三大指數（道瓊／那斯達克／費半）綜合分數的平均值，線性映射到
  1.1x（分數 0）～ 1.0x（分數 50）～ 0.9x（分數 100），對應「待確認事項 #4」收斂後的有界傾斜範圍。
"""
import pandas as pd

from common import TICKERS, DATA_DIR, load_raw, save_json

PCT_WINDOW = 756  # 近 3 年交易日數，用來算百分位排名
PCT_MIN_PERIODS = 252  # 至少要有約 1 年資料才開始給百分位分數
MA_WINDOW = 200

RESAMPLE_RULES = {
    "weekly": "W-FRI",
    "monthly": "ME",
    "quarterly": "QE",
    "yearly": "YE",
}


def to_dataframe(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date").set_index("date")
    return df


def compute_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)  # 資料不足時給中性值，避免前端出現空白


def compute_price_percentile(close):
    return close.rolling(PCT_WINDOW, min_periods=PCT_MIN_PERIODS).rank(pct=True) * 100


def compute_ma_deviation(close):
    ma200 = close.rolling(MA_WINDOW, min_periods=MA_WINDOW).mean()
    dev = (close - ma200) / ma200
    dev_zscore = (dev - dev.rolling(PCT_WINDOW, min_periods=PCT_MIN_PERIODS).mean()) / \
        dev.rolling(PCT_WINDOW, min_periods=PCT_MIN_PERIODS).std()
    dev_pct = dev.rolling(PCT_WINDOW, min_periods=PCT_MIN_PERIODS).rank(pct=True) * 100
    return dev_zscore, dev_pct


def build_ohlc_records(df, cols=("open", "high", "low", "close", "volume")):
    records = []
    for idx, row in df.iterrows():
        rec = {"date": idx.strftime("%Y-%m-%d")}
        for c in cols:
            if c in row and pd.notna(row[c]):
                rec[c] = round(float(row[c]), 4) if c != "volume" else int(row[c])
        records.append(rec)
    return records


def resample_ohlc(df):
    out = {"daily": build_ohlc_records(df)}
    for label, rule in RESAMPLE_RULES.items():
        agg = df.resample(rule).agg({
            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
        }).dropna(subset=["close"])
        out[label] = build_ohlc_records(agg)
    return out


def main():
    vix_rows = load_raw("VIX")
    if not vix_rows:
        raise RuntimeError("找不到 data/raw/VIX.json，請先執行 backfill_history.py")
    vix_df = to_dataframe(vix_rows)
    vix_pct = compute_price_percentile(vix_df["close"])
    vix_temp_latest = 100 - float(vix_pct.iloc[-1]) if pd.notna(vix_pct.iloc[-1]) else 50.0
    vix_latest_date = vix_df.index[-1].strftime("%Y-%m-%d")

    etfs, indices = [], []
    index_composites = []

    for t in TICKERS:
        rows = load_raw(t["id"])
        if not rows:
            print(f"[跳過] {t['id']} 沒有原始資料")
            continue
        df = to_dataframe(rows)

        rsi = compute_rsi(df["close"])
        price_pct = compute_price_percentile(df["close"])
        dev_z, dev_pct = compute_ma_deviation(df["close"])

        composite = (rsi + price_pct.fillna(50) + dev_pct.fillna(50) + vix_temp_latest) / 4

        latest = {
            "id": t["id"],
            "name": t["name"],
            "type": t["type"],
            "date": df.index[-1].strftime("%Y-%m-%d"),
            "close": round(float(df["close"].iloc[-1]), 4),
            "change_pct": round(
                float((df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100), 2
            ) if len(df) > 1 else 0.0,
            "rsi14": round(float(rsi.iloc[-1]), 1),
            "ma200_deviation_zscore": round(float(dev_z.iloc[-1]), 2) if pd.notna(dev_z.iloc[-1]) else None,
            "price_percentile_3y": round(float(price_pct.iloc[-1]), 1) if pd.notna(price_pct.iloc[-1]) else None,
            "composite_score": round(float(composite.iloc[-1]), 1),
        }
        if "proxy_note" in t:
            latest["proxy_note"] = t["proxy_note"]

        record = {**latest, "timeframes": resample_ohlc(df)}

        if t["type"] == "etf":
            etfs.append(record)
        else:
            indices.append(record)
            index_composites.append(latest["composite_score"])

    market_temperature = round(sum(index_composites) / len(index_composites), 1) if index_composites else 50.0
    dca_multiplier = round(1.1 - (market_temperature / 100) * 0.2, 3)

    if market_temperature < 30:
        zone = "相對低檔"
    elif market_temperature > 70:
        zone = "相對高檔"
    else:
        zone = "中性"

    summary = {
        "generated_at_source_date": max(
            [e["date"] for e in etfs + indices] + [vix_latest_date]
        ),
        "market_temperature": market_temperature,
        "market_temperature_zone": zone,
        "dca_multiplier": dca_multiplier,
        "dca_multiplier_range_note": "有界傾斜範圍 0.9x-1.1x（AS-IS 設計，連續公式，未採門檻觸發版本）",
        "vix": {
            "date": vix_latest_date,
            "close": round(float(vix_df["close"].iloc[-1]), 2),
            "percentile_3y": round(float(vix_pct.iloc[-1]), 1) if pd.notna(vix_pct.iloc[-1]) else None,
            "temperature_contribution": round(vix_temp_latest, 1),
        },
        "disclaimer": "本頁所有分數為公開技術指標的客觀呈現，統計上是否能提升定期定額報酬證據薄弱（信心約 45/100），僅供資訊參考，不構成個人化投資建議。",
    }

    save_json(DATA_DIR / "etfs.json", {"items": etfs})
    save_json(DATA_DIR / "indices.json", {"items": indices})
    save_json(DATA_DIR / "summary.json", summary)

    print(f"市場溫度：{market_temperature}（{zone}），建議倍數：{dca_multiplier}x")
    print(f"已輸出 data/etfs.json（{len(etfs)} 檔）、data/indices.json（{len(indices)} 檔）、data/summary.json")


if __name__ == "__main__":
    main()
