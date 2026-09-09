"""
reconcile.py — Gộp 3 nguồn (HNX auctions, HNX yield curve, VBMA activities)
thành 1 bảng dài thống nhất: (date, tenor, rate_type, value, source,
confidence, fetched_at) — đúng data model trong
kien-truc-pipeline-tpcp-dai-han.md, dùng chung cho cả 2 dự án (CW
Black-Scholes và VN30F1M cost-of-carry).

QUAN TRỌNG VỀ PHẦN VBMA: vbma_activities.py CHƯA từng chạy sống để biết
bài "Kết quả đấu thầu..." thật của VBMA có bảng HTML hay chỉ có văn xuôi.
Phần trích tenor/lãi suất từ VBMA dưới đây dùng CÙNG heuristic đã kiểm
chứng cho HNX yield curve (tên cột kiểu "kỳ hạn" / tên kỳ hạn kiểu
"X tháng"/"X năm") — nhưng ĐÂY LÀ BEST-EFFORT, chưa kiểm chứng bằng dữ
liệu sống. Nếu không khớp được, dòng đó bị bỏ qua (rỗng) chứ không suy
đoán bừa — hãy tự in vài dòng `table_json` thật ra xem cấu trúc trước khi
tin tưởng hoàn toàn cột vbma_* trong output.

KHÔNG NỘI SUY NGẦM: reconcile.py chỉ gộp và chuẩn hoá định dạng, không tạo
thêm giá trị nào không có trong nguồn gốc. Muốn có chuỗi liên tục cho
Black-Scholes/cost-of-carry, xử lý interpolation ở một bước RIÊNG sau
reconcile.py, ghi vào cột riêng (`interpolated_value` + cờ
`is_interpolated`), không ghi đè lên `value` gốc.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from common import strip_diacritics, vn_number

KEEP_COLS = ["date", "tenor", "rate_type", "value", "source", "confidence", "fetched_at"]


def _tenor_from_vn_text(s: str) -> str | None:
    s = strip_diacritics(s or "").lower().strip()
    m = re.match(r"(\d+)\s*thang", s)
    if m:
        return f"{m.group(1)}M"
    m = re.match(r"(\d+)\s*nam", s)
    if m:
        return f"{m.group(1)}Y"
    return None


def _from_auctions(cache_dir: Path) -> pd.DataFrame:
    f = cache_dir / "hnx_auctions_daily.parquet"
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_parquet(f)
    if "auction_successful" not in df.columns:
        return pd.DataFrame()
    df = df[df["auction_successful"] == True].copy()  # noqa: E712 — chỉ giữ phiên thật sự có người trúng
    df["tenor"] = df["Kỳ hạn"].map(_tenor_from_vn_text) if "Kỳ hạn" in df.columns else None
    df["date"] = df["trade_date"]
    df["value"] = df["winning_rate_pct"]
    df["rate_type"] = "primary_winning_yield"
    df["source"] = "hnx_auctions"
    df["confidence"] = "official"
    return df[[c for c in KEEP_COLS if c in df.columns]].dropna(subset=["tenor", "value"])


def _from_yield_curve(cache_dir: Path) -> pd.DataFrame:
    f = cache_dir / "hnx_yield_curve_daily.parquet"
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_parquet(f)
    df["source"] = "hnx_yield_curve"
    df["confidence"] = "official"
    return df[[c for c in KEEP_COLS if c in df.columns]]


def _from_vbma(cache_dir: Path) -> pd.DataFrame:
    """BEST-EFFORT — xem cảnh báo ở đầu file, chưa kiểm chứng bằng dữ liệu
    sống. Chỉ xử lý bài có bảng HTML thật (structured=True)."""
    f = cache_dir / "vbma_activities.parquet"
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_parquet(f)
    if "structured" not in df.columns:
        return pd.DataFrame()
    df = df[df["structured"] == True].copy()  # noqa: E712

    rows = []
    for _, r in df.iterrows():
        try:
            table = json.loads(r["table_json"])
        except (TypeError, ValueError):
            continue
        header, body = table.get("header", []), table.get("rows", [])
        if not header or not body:
            continue
        for row in body:
            tenor = _tenor_from_vn_text(row[0]) if row else None
            if not tenor:
                continue
            for i, col_name in enumerate(header[1:], start=1):
                if i >= len(row):
                    continue
                val = vn_number(row[i])
                if val == val:   # loại NaN
                    rate_type = "vbma_" + strip_diacritics(col_name).lower().replace(" ", "_")
                    rows.append({
                        "date": r["date"], "tenor": tenor, "rate_type": rate_type,
                        "value": val, "source": "vbma_activities",
                        "confidence": "cross_check", "fetched_at": r["fetched_at"],
                    })
    return pd.DataFrame(rows, columns=KEEP_COLS)


def run(cache_dir: str | Path = "./cache", *, verbose: bool = True) -> pd.DataFrame:
    cache_dir = Path(cache_dir)
    parts = [_from_auctions(cache_dir), _from_yield_curve(cache_dir), _from_vbma(cache_dir)]
    parts = [p for p in parts if len(p)]
    merged = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=KEEP_COLS)

    if len(merged):
        merged["date"] = pd.to_datetime(merged["date"])
        merged = merged.sort_values(["date", "tenor", "source"]).reset_index(drop=True)

    out_path = cache_dir / "combined_tpcp_yields.parquet"
    merged.to_parquet(out_path, index=False)

    if verbose:
        print(f"Gộp xong: {len(merged)} dòng -> {out_path}")
        if len(merged):
            print("\nSố dòng theo nguồn:")
            print(merged.groupby("source").size())
            print("\nKhoảng ngày phủ được:", merged["date"].min(), "->", merged["date"].max())
    return merged


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Gộp các nguồn TPCP thành 1 bảng dài thống nhất")
    p.add_argument("--cache-dir", default="./cache")
    a = p.parse_args()
    run(a.cache_dir)
