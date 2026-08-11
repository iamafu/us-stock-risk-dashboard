"""季度抓取波克夏最新 13F-HR，算出對最近一期申報的淨買超／賣超方向。

刻意設計成獨立、低頻的季度腳本，不併入每日資料管線：
  - SEC Rule 13f-1 規定機構每季結束後最長 45 天內申報，本來就不是每日更新的資料
  - 13F 反映的是「持有哪些個股」，不是「大盤貴不貴」，只能當輔助脈絡，不進市場溫度分數

只讀取正式 13F-HR（不含修正版 13F-HR/A），比較最新一期與上一期的持股，
用「增持／減持／新進／出清」的檔數做簡單方向判斷，並列出增減幅度最大的持股當背景資訊。
"""
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

import requests

from common import DATA_DIR, save_json

CIK = "1067983"  # Berkshire Hathaway Inc
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{int(CIK):010d}.json"
ARCHIVE_BASE = f"https://www.sec.gov/Archives/edgar/data/{CIK}"

# SEC EDGAR 要求 User-Agent 附上可識別的聯絡資訊，請改成你自己的專案名稱與 email
SEC_HEADERS = {"User-Agent": "us-stock-risk-dashboard/1.0 (contact: replace-with-your-email@example.com)"}

XML_NS = {"t": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}


def get_recent_13f_filings(limit=2):
    r = requests.get(SUBMISSIONS_URL, headers=SEC_HEADERS, timeout=20)
    r.raise_for_status()
    recent = r.json()["filings"]["recent"]
    filings = []
    for i, form in enumerate(recent["form"]):
        if form == "13F-HR":  # 不含 13F-HR/A 修正版，避免重複計入同一期
            filings.append({
                "accessionNumber": recent["accessionNumber"][i],
                "filingDate": recent["filingDate"][i],
                "reportDate": recent["reportDate"][i],
            })
        if len(filings) >= limit:
            break
    return filings


def find_info_table_url(accession_number):
    acc_nodash = accession_number.replace("-", "")
    idx_url = f"{ARCHIVE_BASE}/{acc_nodash}/index.json"
    r = requests.get(idx_url, headers=SEC_HEADERS, timeout=20)
    r.raise_for_status()
    for item in r.json()["directory"]["item"]:
        name = item["name"]
        if name.endswith(".xml") and name != "primary_doc.xml":
            return f"{ARCHIVE_BASE}/{acc_nodash}/{name}"
    raise RuntimeError(f"在 {accession_number} 找不到 information table XML")


def parse_holdings(xml_url):
    r = requests.get(xml_url, headers=SEC_HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)

    holdings = defaultdict(lambda: {"name": "", "shares": 0, "value": 0})
    for info in root.findall("t:infoTable", XML_NS):
        cusip = info.findtext("t:cusip", default="", namespaces=XML_NS)
        name = info.findtext("t:nameOfIssuer", default="", namespaces=XML_NS)
        shares_el = info.find("t:shrsOrPrnAmt/t:sshPrnamt", XML_NS)
        value_el = info.find("t:value", XML_NS)
        shares = int(shares_el.text) if shares_el is not None and shares_el.text else 0
        value = int(value_el.text) if value_el is not None and value_el.text else 0
        # 波克夏由多個子公司分開申報同一檔持股，需依 cusip 加總才是總部位
        holdings[cusip]["name"] = name
        holdings[cusip]["shares"] += shares
        holdings[cusip]["value"] += value
    return dict(holdings)


def compare_holdings(latest, previous):
    all_cusips = set(latest) | set(previous)
    increased, decreased, new, sold_out = [], [], [], []

    for cusip in all_cusips:
        cur = latest.get(cusip)
        prev = previous.get(cusip)
        if cur and not prev:
            new.append({"name": cur["name"], "shares": cur["shares"]})
        elif prev and not cur:
            sold_out.append({"name": prev["name"], "shares": prev["shares"]})
        elif cur and prev:
            if cur["shares"] > prev["shares"]:
                pct = (cur["shares"] / prev["shares"] - 1) * 100 if prev["shares"] else None
                increased.append({"name": cur["name"], "shares": cur["shares"], "change_pct": pct})
            elif cur["shares"] < prev["shares"]:
                pct = (cur["shares"] / prev["shares"] - 1) * 100 if prev["shares"] else None
                decreased.append({"name": cur["name"], "shares": cur["shares"], "change_pct": pct})

    bullish_count = len(increased) + len(new)
    bearish_count = len(decreased) + len(sold_out)
    if bullish_count > bearish_count:
        direction = "淨買超"
    elif bearish_count > bullish_count:
        direction = "淨賣超"
    else:
        direction = "持平"

    increased.sort(key=lambda x: x["change_pct"] or 0, reverse=True)
    decreased.sort(key=lambda x: x["change_pct"] or 0)

    return {
        "direction": direction,
        "holdings_count_latest": len(latest),
        "holdings_count_previous": len(previous),
        "increased_count": len(increased),
        "decreased_count": len(decreased),
        "new_count": len(new),
        "sold_out_count": len(sold_out),
        "top_increased": increased[:3],
        "top_new": new[:3],
        "top_decreased": decreased[:3],
        "top_sold_out": sold_out[:3],
        "total_value_latest": sum(h["value"] for h in latest.values()),
        "total_value_previous": sum(h["value"] for h in previous.values()),
    }


def main():
    filings = get_recent_13f_filings(limit=2)
    if len(filings) < 2:
        raise RuntimeError("抓不到足夠的 13F-HR 申報紀錄做比較（需要至少 2 期）")

    latest_filing, previous_filing = filings[0], filings[1]
    print(f"最新一期：{latest_filing['filingDate']}（報告期 {latest_filing['reportDate']}）")
    print(f"對照上一期：{previous_filing['filingDate']}（報告期 {previous_filing['reportDate']}）")

    latest_holdings = parse_holdings(find_info_table_url(latest_filing["accessionNumber"]))
    previous_holdings = parse_holdings(find_info_table_url(previous_filing["accessionNumber"]))

    comparison = compare_holdings(latest_holdings, previous_holdings)

    output = {
        "filing_date": latest_filing["filingDate"],
        "report_period_end": latest_filing["reportDate"],
        "compared_to_report_period_end": previous_filing["reportDate"],
        "note": "13F 依 SEC 規定每季申報、最長落後申報截止日 45 天，僅反映個股買賣，非大盤估值，"
                "不併入每日市場溫度分數，僅作季度低頻脈絡參考。",
        **comparison,
    }

    save_json(DATA_DIR / "berkshire_13f.json", output)
    print(f"\n方向：{comparison['direction']}（增持/新進 {comparison['increased_count'] + comparison['new_count']} "
          f"檔 vs 減持/出清 {comparison['decreased_count'] + comparison['sold_out_count']} 檔）")
    print("已輸出 data/berkshire_13f.json")


if __name__ == "__main__":
    main()
