"""
hnx_foreign_daily.py — Lấy thống kê giao dịch theo nhà đầu tư từ HNX.

Dữ liệu khối ngoại (NĐTNN) không nằm trong API HTML thông thường của HNX mà
được publish dưới dạng file PDF tĩnh hàng ngày trên máy chủ FTP open-web
của HNX (owa.hnx.vn). Module này tải PDF về và dùng pdfplumber để bóc tách
thành bảng dữ liệu.

OUTPUT:
    File `cache/hnx_foreign_daily.parquet`.
    Schema khớp với yêu cầu của `analysis/foreign_flow.py`:
    trade_date | tenor_label | tenor_years |
    dom_buy_vol | dom_buy_vnd | dom_sell_vol | dom_sell_vnd |
    for_buy_vol | for_buy_vnd | for_sell_vol | for_sell_vnd |
    for_net_bn
"""
from __future__ import annotations

import argparse
import io
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import urllib3

from pipeline.common import daily_scan, vn_number

urllib3.disable_warnings()

DATA_FILE = "hnx_foreign_daily.parquet"
CHECKED_FILE = "hnx_foreign_checked_dates.parquet"
DATE_COL = "trade_date"
DEDUP_KEYS = ["trade_date", "tenor_label"]

_PDF_TABLE = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "intersection_tolerance": 5
}


def _tenor_years(s: str) -> float | None:
    if pd.isna(s):
        return None
    s = str(s).lower().strip()
    m = re.search(r"(\d+)\s*n[ăa]m", s)
    if m:
        return float(m.group(1))
    return None


def fetch_foreign_pdf(d: date, sess: requests.Session) -> pd.DataFrame:
    """Tải và parse PDF thống kê giao dịch khối ngoại của một ngày."""
    ds = d.strftime("%Y%m%d")
    url = (f"https://owa.hnx.vn/ftp///THONGKEGIAODICH//{ds}/TP/"
           f"{ds}_TP_Thong_ke_giao_dich_outright_theo_ndt.pdf")

    try:
        r = sess.get(url, timeout=30, verify=False)
    except Exception:
        return pd.DataFrame()

    if r.status_code != 200 or r.content[:4] != b"%PDF":
        return pd.DataFrame()

    try:
        import pdfplumber
    except ImportError:
        print("CẢNH BÁO: Cần cài đặt pdfplumber (pip install pdfplumber) để đọc báo cáo khối ngoại.")
        return pd.DataFrame()

    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        if not pdf.pages:
            return pd.DataFrame()
        tables = pdf.pages[0].extract_tables(table_settings=_PDF_TABLE)

    if not tables:
        return pd.DataFrame()

    raw = pd.DataFrame(tables[0])
    if raw.shape[1] < 10:
        return pd.DataFrame()

    # HNX PDF có header 2 tầng, data thực bắt đầu từ dòng 2 (index 2)
    body = raw.iloc[2:].reset_index(drop=True)
    if len(body) and str(body.iloc[-1, 0]).strip().lower().startswith(("tổng", "tong")):
        body = body.iloc[:-1]  # Bỏ hàng tính tổng
        
    # Lọc bỏ các hàng rỗng
    body = body[body[0].notna() & (body[0].astype(str).str.strip() != "")]
    if body.empty:
        return pd.DataFrame()

    def col(k):
        return body[k].map(vn_number)

    out = pd.DataFrame({
        "trade_date": pd.Timestamp(d),
        "tenor_label": body[0].astype(str).str.strip(),
        "dom_buy_vol": col(2), "dom_buy_vnd": col(3),
        "dom_sell_vol": col(4), "dom_sell_vnd": col(5),
        "for_buy_vol": col(6), "for_buy_vnd": col(7),
        "for_sell_vol": col(8), "for_sell_vnd": col(9),
    })
    
    out["tenor_years"] = out["tenor_label"].map(_tenor_years)
    out["for_net_bn"] = (out["for_buy_vnd"].fillna(0) - out["for_sell_vnd"].fillna(0)) / 1e9
    
    # Bổ sung meta
    out["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    out["source"] = "hnx_owa_pdf"
    
    return out.reset_index(drop=True)


def run(start: str | date, end: str | date | None = None, *,
        cache_dir: str | Path = "./cache", verbose: bool = True,
        save_every: int = 20) -> pd.DataFrame:
        
    cache_dir = Path(cache_dir)
    if isinstance(start, str):
        d0 = datetime.strptime(start[:10], "%Y-%m-%d").date()
    else:
        d0 = start
        
    if end is None:
        d1 = date.today()
    elif isinstance(end, str):
        d1 = datetime.strptime(end[:10], "%Y-%m-%d").date()
    else:
        d1 = end

    # Dùng requests.Session tĩnh vì owa.hnx.vn là FTP static file, 
    # không có throttling nghiêm ngặt như trang chủ HNX
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.hnx.vn/"
    })

    return daily_scan(
        fetch_foreign_pdf, start=d0, end=d1, sess=sess, cache_dir=cache_dir,
        data_file=DATA_FILE, checked_file=CHECKED_FILE,
        dedup_keys=DEDUP_KEYS, date_col=DATE_COL,
        save_every=save_every, verbose=verbose,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Lấy dữ liệu khối ngoại HNX qua PDF")
    p.add_argument("start", help="Ngày bắt đầu, vd 2026-08-01")
    p.add_argument("end", nargs="?", default=None, help="Ngày kết thúc, mặc định hôm nay")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--save-every", type=int, default=20)
    a = p.parse_args()
    
    run(a.start, a.end, cache_dir=a.cache_dir, save_every=a.save_every)
