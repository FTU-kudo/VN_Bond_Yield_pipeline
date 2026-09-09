"""
hnx_yield_curve_daily.py — Lấy đường cong lợi suất TPCP chính thức từ HNX,
theo TỪNG NGÀY LÀM VIỆC.

ĐẶC ĐIỂM ĐÃ XÁC NHẬN THỰC NGHIỆM (khác hẳn auctions()):
    - KHÔNG có bug giới hạn dòng — mỗi ngày trả về đúng 1 bảng ~11 kỳ hạn,
      không cần chia nhỏ như auctions().
    - NHƯNG độ phủ theo thời gian rất "lởm chởm": có dữ liệu thật từ
      khoảng 2012-2014, thưa và KHÔNG đều tới tận 2018-2019 (một ngày cụ
      thể có thể có curve trong khi ngày liền kề lại không, tuỳ thanh
      khoản thị trường thứ cấp hôm đó — không phải cắt theo mốc năm gọn
      gàng), và chỉ thực sự dày đặc/gần như hàng ngày từ 2020 trở đi.
      PHẦN LỚN NGÀY RỖNG Ở GIAI ĐOẠN 2012-2019 LÀ THẬT — không coi là lỗi,
      không được nội suy để "lấp đầy" nhìn cho đẹp.
    - Chưa xác nhận tên id bảng HTML trả về (endpoint Bond_YieldCurve khác
      hẳn auctions() dùng id="_tableDatas") — dùng bộ trích bảng TỔNG QUÁT
      trong common.py thay vì vnbond.sources.hnx.parse_table.

OUTPUT: dạng "long" — mỗi dòng là (date, tenor, rate_type, value), khớp
    thẳng với data model trong kien-truc-pipeline-tpcp-dai-han.md, để gộp
    với các nguồn khác ở bước reconcile.py mà không cần biến đổi thêm.
"""
from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from vnbond import http
from vnbond.sources.hnx import _as_date

from common import daily_scan, extract_all_tables, strip_diacritics, vn_number

YIELD_CURVE_URL = "https://www.hnx.vn/ModuleReportBonds/Bond_YieldCurve/SearchAndNextPageYieldCurveData"
REF_YIELD_CURVE = "https://www.hnx.vn/vi-vn/trai-phieu/duong-cong-loi-suat.html"

DATA_FILE = "hnx_yield_curve_daily.parquet"
CHECKED_FILE = "hnx_yield_curve_checked_dates.parquet"
DATE_COL = "date"
DEDUP_KEYS = ["date", "tenor", "rate_type"]


def _rate_type_from_header(h: str) -> str | None:
    h = strip_diacritics(h).lower()
    if "lien tuc" in h:
        return "spot_continuous"
    if "par" in h:
        return "par_yield"
    if "theo nam" in h or "annual" in h:
        return "spot_annual"
    return None


def _parse_tenor(s: str) -> str | None:
    s = strip_diacritics(s).lower().strip()
    m = re.match(r"(\d+)\s*thang", s)
    if m:
        return f"{m.group(1)}M"
    m = re.match(r"(\d+)\s*nam", s)
    if m:
        return f"{m.group(1)}Y"
    return None


def fetch_yield_curve_day(d: date, sess) -> pd.DataFrame:
    """Đường cong lợi suất của MỘT ngày, dạng long. DataFrame rỗng nếu
    ngày đó không có curve — điều này BÌNH THƯỜNG, đặc biệt trước 2020."""
    r = http.post(YIELD_CURVE_URL, session=sess, timeout=60,
                  data={"pDate": f"{d:%d/%m/%Y}"})
    tables = extract_all_tables(r.text)
    if not tables or len(tables[0]) < 2:
        return pd.DataFrame()   # chỉ có header hoặc không tìm thấy bảng nào

    header, *body = tables[0]
    rate_cols = [(i, _rate_type_from_header(h)) for i, h in enumerate(header[1:], start=1)]
    rate_cols = [(i, rt) for i, rt in rate_cols if rt]
    if not rate_cols:
        return pd.DataFrame()

    out = []
    for row in body:
        tenor = _parse_tenor(row[0]) if row else None
        if not tenor:
            continue
        for i, rate_type in rate_cols:
            if i >= len(row):
                continue
            val = vn_number(row[i])
            if val == val:   # loại NaN mà không cần import math riêng
                out.append({"date": pd.Timestamp(d), "tenor": tenor,
                            "rate_type": rate_type, "value": val})

    df = pd.DataFrame(out)
    if len(df):
        df["fetched_at"] = datetime.now().isoformat(timespec="seconds")
        df["source"] = "hnx_yield_curve"
    return df


def run(start: str | date, end: str | date | None = None, *,
        cache_dir: str | Path = "./cache", verbose: bool = True,
        save_every: int = 20) -> pd.DataFrame:
    cache_dir = Path(cache_dir)
    d0, d1 = _as_date(start), _as_date(end or date.today())
    sess = http.session(REF_YIELD_CURVE)
    return daily_scan(
        fetch_yield_curve_day, start=d0, end=d1, sess=sess, cache_dir=cache_dir,
        data_file=DATA_FILE, checked_file=CHECKED_FILE,
        dedup_keys=DEDUP_KEYS, date_col=DATE_COL,
        save_every=save_every, verbose=verbose,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Lấy đường cong lợi suất TPCP từ HNX theo ngày")
    p.add_argument("start", help="Ngày bắt đầu, vd 2012-01-01")
    p.add_argument("end", nargs="?", default=None, help="Ngày kết thúc, mặc định hôm nay")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--save-every", type=int, default=20)
    a = p.parse_args()
    run(a.start, a.end, cache_dir=a.cache_dir, save_every=a.save_every)
