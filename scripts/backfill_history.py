"""一次性回補近 20 年日線歷史資料。

刻意全部走 yfinance（免金鑰、無日配額限制），把 Twelve Data 有限的免費額度留給
每日增量抓取使用——一次性大量回補如果打在 Twelve Data 免費層，會浪費掉好幾天的額度。
此腳本只需在專案初始化時手動執行一次，之後改由 fetch_data.py 做每日增量。

注意：VOO（2010 上市）、VT（2008 上市）的實際歷史都不滿 20 年，yfinance 會直接回傳
從上市日開始有的全部資料，不會報錯，只是筆數比其他商品少——這是正常現象，不是抓取失敗。
"""
import sys
from datetime import datetime, timedelta

import yfinance as yf

from common import TICKERS, VIX_YF_SYMBOL, save_raw, ensure_dirs

YEARS_BACK = 20


def fetch_history(yf_symbol, start, end):
    df = yf.Ticker(yf_symbol).history(start=start, end=end, interval="1d", auto_adjust=False)
    rows = []
    for idx, row in df.iterrows():
        rows.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 4),
            "high": round(float(row["High"]), 4),
            "low": round(float(row["Low"]), 4),
            "close": round(float(row["Close"]), 4),
            "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
            "source": "yfinance",
        })
    return rows


def main():
    ensure_dirs()
    end = datetime.today()
    start = end - timedelta(days=YEARS_BACK * 365 + 30)

    failures = []
    for t in TICKERS:
        print(f"回補 {t['id']}（{t['yf_symbol']}）...")
        try:
            rows = fetch_history(t["yf_symbol"], start, end)
            if not rows:
                raise RuntimeError("回傳資料為空")
            save_raw(t["id"], rows)
            print(f"  完成，共 {len(rows)} 筆，{rows[0]['date']} ~ {rows[-1]['date']}")
        except Exception as e:  # noqa: BLE001
            print(f"  失敗：{e}")
            failures.append(t["id"])

    print(f"回補 VIX（{VIX_YF_SYMBOL}）...")
    try:
        rows = fetch_history(VIX_YF_SYMBOL, start, end)
        save_raw("VIX", rows)
        print(f"  完成，共 {len(rows)} 筆")
    except Exception as e:  # noqa: BLE001
        print(f"  失敗：{e}")
        failures.append("VIX")

    if failures:
        print(f"\n以下代號回補失敗，需重新執行或手動檢查：{failures}")
        sys.exit(1)
    print("\n全部回補完成。")


if __name__ == "__main__":
    main()
