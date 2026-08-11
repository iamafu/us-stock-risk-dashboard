# 美股 ETF 定期定額監控儀表板

追蹤 VTI／VOO／IVV／SPY／QQQ／VT 六檔 ETF，並以道瓊工業指數、那斯達克綜合指數、費城半導體指數三大指數的 RSI-14、200 日均線乖離率、近 3 年價格百分位排名、VIX 恐慌指數，等權重合成 0–100 的「市場溫度分數」，作為每月三次定期定額扣款的權重參考（0.9x–1.1x 有界傾斜）。歷史資料回補近 **20 年**日線，並依日／週／月／季／年多週期彙總。另外提供波克夏 13F 季度低頻脈絡卡。

**這是個人資訊工具，不是投資顧問服務。** 所有分數都是公開技術指標的客觀呈現；統計上這類訊號能否提升定期定額報酬，證據目前偏薄弱（信心約 45/100），儀表板刻意用中性客觀用語呈現，不做「加碼／減碼」的勸誘式建議。

## 架構

```
scripts/
  common.py              # 共用設定：代號清單、路徑、Twelve Data / 重試工具
  backfill_history.py    # 一次性回補近 20 年日線（走 yfinance，省 Twelve Data 額度）
  fetch_data.py           # 每日增量抓取（6 檔 ETF 走 Twelve Data + yfinance 交叉驗證；3 大指數走 yfinance，Twelve Data 免費層不含指數資料）
  compute_indicators.py   # 計算 RSI／乖離率／百分位／VIX／綜合分數，輸出多週期 JSON
  fetch_13f.py             # 季度抓取波克夏 13F，算淨買超/賣超方向
.github/workflows/
  update-data.yml          # 每日排程（America/New_York 17:30 收盤後）
  update-13f.yml            # 每週一檢查一次是否有新的 13F 申報
data/                       # 各腳本輸出的 JSON（etfs.json / indices.json / summary.json / berkshire_13f.json / raw/）
index.html, assets/         # 前端頁面（儀表刻度風格，純 HTML/CSS/JS + Chart.js CDN）
```

## 本機設定步驟

1. 安裝相依套件：
   ```bash
   pip install -r requirements.txt
   ```
2. 一次性回補歷史資料（只需執行一次）：
   ```bash
   cd scripts
   python backfill_history.py
   ```
3. 計算指標並產生前端要用的 JSON：
   ```bash
   python compute_indicators.py
   ```
4. （選用）抓波克夏 13F 脈絡卡：
   ```bash
   python fetch_13f.py
   ```
   `fetch_13f.py` 內的 `SEC_HEADERS` 請改成你自己的聯絡資訊（SEC EDGAR 規定 User-Agent 需可識別，不能用預留位置的 email）。
5. 本機預覽：在專案根目錄啟動一個靜態伺服器（例如 `python -m http.server 8000`），瀏覽 `http://localhost:8000`。**不能直接用 `file://` 開啟 `index.html`**，瀏覽器會擋掉本地 JSON 的 fetch 請求。

## 部署到 GitHub Pages（尚未執行，需要你確認）

1. 到 [Twelve Data](https://twelvedata.com/) 免費註冊帳號，取得 API Key。
2. 在 GitHub repo 的 Settings → Secrets and variables → Actions 新增 `TWELVEDATA_API_KEY`。
3. Settings → Actions → General，把 Workflow permissions 設為「Read and write permissions」，`update-data.yml` 才能把資料 commit 回 repo。
4. Settings → Pages，Source 選 `Deploy from a branch`，分支選 `main`、目錄選 `/ (root)`。
5. 首次部署前，先在本機執行過 `backfill_history.py` 與 `compute_indicators.py`，把產生的 `data/` 一併 commit，讓網站一上線就有資料，之後才交給每日排程增量更新。

repo 建議設為 **Public**（GitHub Pages 與 Actions 分鐘數才能全免費；Private repo 需要 GitHub Pro 才能用 Pages）。

## 已知限制

- Twelve Data 免費層實測**不含原始指數資料**（`DJI`／`IXIC` 等符號一律回 404 invalid symbol，不是額度問題），因此三大指數改為直接以 yfinance 為主要來源，只有 6 檔 ETF 走 Twelve Data。
- VOO（2010-09 上市）、VT（2008-06 上市）的實際歷史不滿 20 年，圖表會從上市日開始顯示，屬正常現象。
- 每月 3 次扣款日期目前是佔位（5 / 15 / 25 日），實際日期與金額請自行依需求調整 `assets/js/dashboard.js` 裡 `renderDCA()` 的 `dates` 陣列。
- 波克夏 13F 依 SEC 規定每季申報、最長落後申報截止日 45 天，僅反映個股買賣方向，不代表大盤估值，不會被計入市場溫度分數。
