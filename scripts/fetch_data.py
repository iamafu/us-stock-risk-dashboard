"""每日增量抓取。

實測確認：Twelve Data 免費層的原始指數（道瓊、那斯達克、費半）一律回 404，不是額度問題，
是免費層根本不含指數資料，因此指數改走 yfinance 為主要來源；6 檔 ETF 則走 Twelve Data
（6 credits，一次請求就在免費層每分鐘 8 credits 上限內），並用 yfinance 交叉比對。

若環境沒有 TWELVEDATA_API_KEY（例如本機開發、還沒申請金鑰），ETF 也自動改用 yfinance，
讓開發階段不需要金鑰也能跑通全流程。
"""
import sys

import yfinance as yf

from common import (
    TWELVEDATA_TICKERS,
    YFINANCE_ONLY_TICKERS,
    VIX_YF_SYMBOL,
    TWELVEDATA_API_KEY,
    CROSS_CHECK_TOLERANCE,
    load_raw,
    save_raw,
    twelvedata_batch_quote,
)


def yfinance_latest_bar(yf_symbol):
    """抓最近幾天日線，回傳最新一筆完整交易日的 OHLCV（避開當日盤中未收盤資料）。"""
    df = yf.Ticker(yf_symbol).history(period="10d", interval="1d", auto_adjust=False)
    if df.empty:
        return None
    row = df.iloc[-1]
    idx = df.index[-1]
    return {
        "date": idx.strftime("%Y-%m-%d"),
        "open": round(float(row["Open"]), 4),
        "high": round(float(row["High"]), 4),
        "low": round(float(row["Low"]), 4),
        "close": round(float(row["Close"]), 4),
        "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
        "source": "yfinance",
    }


def twelvedata_quote_to_bar(quote):
    """把 Twelve Data /quote 回應轉成跟 yfinance 一致的格式；缺欄位或錯誤回傳 None。"""
    if not isinstance(quote, dict) or "close" not in quote:
        return None
    dt = quote.get("datetime") or quote.get("last_quote_at")
    if not dt:
        return None
    date_str = dt.split(" ")[0][:10]
    try:
        return {
            "date": date_str,
            "open": round(float(quote["open"]), 4),
            "high": round(float(quote["high"]), 4),
            "low": round(float(quote["low"]), 4),
            "close": round(float(quote["close"]), 4),
            "volume": int(float(quote.get("volume") or 0)),
            "source": "twelvedata",
        }
    except (KeyError, TypeError, ValueError):
        return None


def cross_check(primary_bar, secondary_bar):
    """雙來源交叉比對收盤價，落差超過容許誤差則標記 uncertain，而不是靜默採用主來源。"""
    if primary_bar is None:
        return secondary_bar, ("missing_primary" if secondary_bar else "missing_both")
    if secondary_bar is None:
        return primary_bar, "unverified_single_source"
    diff = abs(primary_bar["close"] - secondary_bar["close"]) / secondary_bar["close"]
    if diff > CROSS_CHECK_TOLERANCE:
        return primary_bar, f"uncertain_diff_{diff:.2%}"
    return primary_bar, "ok"


def fetch_via_twelvedata_batch(tickers):
    symbols = [t["td_symbol"] for t in tickers]
    raw = twelvedata_batch_quote(symbols)  # 已在 common.py 依 credit 上限分批＋合併結果
    return {t["id"]: twelvedata_quote_to_bar(raw.get(t["td_symbol"])) for t in tickers}


def save_if_new(ticker_id, bar, updated_list):
    rows = load_raw(ticker_id)
    if bar["date"] in {r["date"] for r in rows}:
        print(f"[略過] {ticker_id} {bar['date']} 已存在，非新交易日")
        return
    rows.append(bar)
    save_raw(ticker_id, rows)
    updated_list.append(f"{ticker_id}:{bar['date']}")


def main():
    use_twelvedata = bool(TWELVEDATA_API_KEY)
    if not use_twelvedata:
        print("[警告] 未設定 TWELVEDATA_API_KEY，6 檔 ETF 這次也改用 yfinance（僅供本機開發測試）。")

    updated, skipped, uncertain = [], [], []

    # 6 檔 ETF：Twelve Data 為主，yfinance 交叉驗證
    td_bars = {}
    if use_twelvedata:
        try:
            td_bars = fetch_via_twelvedata_batch(TWELVEDATA_TICKERS)
        except Exception as e:  # noqa: BLE001
            print(f"[錯誤] Twelve Data 批次請求整批失敗，這次 ETF 改全部用 yfinance 備援：{e}")
            td_bars = {}

    for t in TWELVEDATA_TICKERS:
        yf_bar = None
        try:
            yf_bar = yfinance_latest_bar(t["yf_symbol"])
        except Exception as e:  # noqa: BLE001
            print(f"[錯誤] yfinance 抓取 {t['id']} 失敗：{e}")

        primary = td_bars.get(t["id"]) if use_twelvedata else None
        bar, status = cross_check(primary, yf_bar) if use_twelvedata else (yf_bar, "yfinance_only")

        if bar is None:
            print(f"[跳過] {t['id']} 兩個來源都拿不到資料")
            skipped.append(t["id"])
            continue
        bar["data_quality"] = status
        if status.startswith("uncertain") or status.startswith("missing"):
            uncertain.append(f"{t['id']}:{status}")
        save_if_new(t["id"], bar, updated)

    # 3 大指數：Twelve Data 免費層不含指數資料，直接以 yfinance 為主要來源
    for t in YFINANCE_ONLY_TICKERS:
        try:
            bar = yfinance_latest_bar(t["yf_symbol"])
        except Exception as e:  # noqa: BLE001
            print(f"[錯誤] yfinance 抓取 {t['id']} 失敗：{e}")
            skipped.append(t["id"])
            continue
        if bar is None:
            skipped.append(t["id"])
            continue
        bar["data_quality"] = "yfinance_only"
        save_if_new(t["id"], bar, updated)

    # VIX 全域情緒指標，只走 yfinance
    try:
        vix_bar = yfinance_latest_bar(VIX_YF_SYMBOL)
        if vix_bar:
            vix_bar["data_quality"] = "yfinance_only"
            save_if_new("VIX", vix_bar, updated)
    except Exception as e:  # noqa: BLE001
        print(f"[錯誤] VIX 抓取失敗：{e}")

    print(f"\n更新：{updated}")
    if uncertain:
        print(f"資料品質待確認：{uncertain}")
    if skipped:
        print(f"完全抓取失敗：{skipped}")

    if not updated:
        print("今天沒有新資料（可能是非交易日），不需要 commit。")
        sys.exit(0)


if __name__ == "__main__":
    main()
