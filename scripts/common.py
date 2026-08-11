"""共用設定與工具函式：代號清單、路徑、重試機制。"""
import json
import os
import time
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
TWELVEDATA_BASE_URL = "https://api.twelvedata.com"

# 6 檔 ETF + 道瓊 + 那斯達克 + 費半（以 SOXX 為主要代理），共 9 檔走 Twelve Data 批次請求
# yf_symbol 是每檔在 yfinance 的備援/交叉驗證代號
# 實測結果（用真實 API Key 打過）：Twelve Data 免費層的 /quote 對 DJI、IXIC、^SOX 這類原始指數
# 一律回 404 "invalid symbol"，symbol_search 也查不到——不是額度或分批的問題，是免費層根本不含指數資料。
# 因此指數（含費半，不再需要 SOXX 代理，^SOX 本身透過 yfinance 就能正常拿到）一律走 yfinance 為主要來源，
# 只有 6 檔 ETF 走 Twelve Data（6 個代號＝6 credits，一次請求就在每分鐘 8 credits 的免費上限內，不需要分批等待）。
TICKERS = [
    {"id": "VTI", "name": "Vanguard 全市場",   "type": "etf",   "source": "twelvedata", "td_symbol": "VTI",  "yf_symbol": "VTI"},
    {"id": "VOO", "name": "Vanguard S&P 500",   "type": "etf",   "source": "twelvedata", "td_symbol": "VOO",  "yf_symbol": "VOO"},
    {"id": "IVV", "name": "iShares S&P 500",    "type": "etf",   "source": "twelvedata", "td_symbol": "IVV",  "yf_symbol": "IVV"},
    {"id": "SPY", "name": "SPDR S&P 500",       "type": "etf",   "source": "twelvedata", "td_symbol": "SPY",  "yf_symbol": "SPY"},
    {"id": "QQQ", "name": "Invesco NASDAQ 100", "type": "etf",   "source": "twelvedata", "td_symbol": "QQQ",  "yf_symbol": "QQQ"},
    {"id": "VT",  "name": "Vanguard 全世界",     "type": "etf",   "source": "twelvedata", "td_symbol": "VT",   "yf_symbol": "VT"},
    {"id": "DJI", "name": "道瓊工業指數",         "type": "index", "source": "yfinance",   "yf_symbol": "^DJI"},
    {"id": "IXIC","name": "那斯達克綜合指數",     "type": "index", "source": "yfinance",   "yf_symbol": "^IXIC"},
    {"id": "SOX", "name": "費城半導體指數",       "type": "index", "source": "yfinance",   "yf_symbol": "^SOX"},
]

TWELVEDATA_TICKERS = [t for t in TICKERS if t["source"] == "twelvedata"]
YFINANCE_ONLY_TICKERS = [t for t in TICKERS if t["source"] == "yfinance"]

# VIX 為全域情緒指標，不分商品，只透過 yfinance 取得（Twelve Data 免費層指數涵蓋不穩定，且只需一組全域數值）
VIX_YF_SYMBOL = "^VIX"

TICKER_IDS = [t["id"] for t in TICKERS]

# 雙來源交叉比對容許誤差（收盤價相對差異超過此比例則標記 data_quality: "uncertain"）
CROSS_CHECK_TOLERANCE = 0.01


def ensure_dirs():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def raw_path(ticker_id):
    return RAW_DIR / f"{ticker_id}.json"


def load_raw(ticker_id):
    p = raw_path(ticker_id)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_raw(ticker_id, rows):
    ensure_dirs()
    rows = sorted(rows, key=lambda r: r["date"])
    with open(raw_path(ticker_id), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=None)


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


TWELVEDATA_CREDITS_PER_MINUTE = 8  # 免費層硬限制：每個代號＝1 credit，跟打幾次請求無關
TWELVEDATA_CHUNK_SIZE = 7  # 留一點餘裕，不要卡在剛好等於上限
TWELVEDATA_CHUNK_WAIT_SECONDS = 65  # 跨過下一個整分鐘視窗，比重試更有效


def retry(fn, attempts=3, base_delay=2.0, label=""):
    """指數退避重試，僅用於暫時性的網路錯誤。

    注意：Twelve Data 免費層的 429（每分鐘 credit 用完）**不該**用短退避重試——
    同一分鐘內重試只會再把僅剩的額度燒光（實測過：一次 9 個代號的請求配上重試，
    一分鐘內就燒了 19 credits，遠超過 8 的上限）。429 交給呼叫端處理分批＋跨分鐘等待，
    這裡只重試網路逾時／連線錯誤等真正的暫時性問題。
    """
    last_err = None
    for i in range(attempts):
        try:
            return fn()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                raise  # 429 不重試，直接往上拋讓呼叫端分批處理
            last_err = e
        except Exception as e:  # noqa: BLE001 - 捕捉其他網路/解析錯誤並重試
            last_err = e
        if i < attempts - 1:
            delay = base_delay * (2 ** i)
            print(f"[retry] {label} 第 {i + 1} 次失敗：{last_err}，{delay:.0f} 秒後重試")
            time.sleep(delay)
    raise last_err


def _twelvedata_quote_call(symbols):
    resp = requests.get(
        f"{TWELVEDATA_BASE_URL}/quote",
        params={"symbol": ",".join(symbols), "apikey": TWELVEDATA_API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def twelvedata_batch_quote(symbols):
    """把代號依免費層每分鐘 credit 上限分批請求，批次之間跨分鐘等待，而不是單批打包全部再重試。"""
    if not TWELVEDATA_API_KEY:
        raise RuntimeError("缺少 TWELVEDATA_API_KEY 環境變數")

    chunks = [symbols[i:i + TWELVEDATA_CHUNK_SIZE] for i in range(0, len(symbols), TWELVEDATA_CHUNK_SIZE)]
    merged = {}
    for idx, chunk in enumerate(chunks):
        if idx > 0:
            print(f"[twelvedata] 等待 {TWELVEDATA_CHUNK_WAIT_SECONDS} 秒跨過下一個 credit 視窗...")
            time.sleep(TWELVEDATA_CHUNK_WAIT_SECONDS)
        raw = retry(lambda c=chunk: _twelvedata_quote_call(c), label=f"twelvedata_batch_quote[{idx}]")
        if "symbol" in raw:
            raw = {raw["symbol"]: raw}
        merged.update(raw)
    return merged


def twelvedata_time_series(symbol, outputsize=30, interval="1day"):
    if not TWELVEDATA_API_KEY:
        raise RuntimeError("缺少 TWELVEDATA_API_KEY 環境變數")

    def _call():
        resp = requests.get(
            f"{TWELVEDATA_BASE_URL}/time_series",
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "apikey": TWELVEDATA_API_KEY,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    return retry(_call, label=f"twelvedata_time_series:{symbol}")
