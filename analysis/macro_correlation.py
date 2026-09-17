"""
macro_correlation.py — Tương quan lợi suất TPCP với các chỉ số vĩ mô.
=======================================================================

Các tương quan được phân tích:
    1. Lãi suất tái cấp vốn SBV ↔ lợi suất TPCP ngắn hạn (1Y, 2Y)
    2. CPI (lạm phát) ↔ real yield và yield dài hạn
    3. Tỷ giá USD/VND ↔ yield (NĐT ngoại sensitivity)
    4. VN-Index ↔ yield dài hạn (flight-to-quality)

Nguồn dữ liệu vĩ mô:
    - Các chỉ số từ nguồn CÔNG KHAI (SBV, GSO) — không cần API key
    - Sử dụng pandas_datareader + requests trực tiếp
    - Tỷ giá USD/VND: lấy từ SBV (public endpoint)

LƯU Ý QUAN TRỌNG VỀ CAUSAL INFERENCE:
    Tương quan ≠ nhân quả. Phân tích này chỉ là mô tả thống kê.
    Lag analysis (Granger causality) cần nghiên cứu riêng trước khi
    đưa ra kết luận về chiều tác động.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np
import pandas as pd
import requests


# ── Lấy dữ liệu vĩ mô công khai ─────────────────────────────────────────────

def fetch_usdvnd_sbv(
    start: str = "2018-01-01",
    end: str | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Tỷ giá trung tâm USD/VND từ SBV."""
    if verbose:
        print("Đang lấy tỷ giá USD/VND qua yfinance...")

    try:
        import yfinance as yf
        ticker = yf.Ticker("USDVND=X")
        hist = ticker.history(start=start, end=end or date.today().isoformat())
        if not hist.empty:
            rows_df = hist[["Close"]].reset_index()
            rows_df.columns = ["date", "usdvnd"]
            rows_df["date"] = pd.to_datetime(rows_df["date"]).dt.date
            if verbose:
                print(f"  USD/VND từ Yahoo Finance: {len(rows_df)} ngày")
            return rows_df
    except Exception as e:
        if verbose:
            print(f"  Không thể lấy USD/VND từ Yahoo Finance: {e}")

    if verbose:
        print("  Không có dữ liệu USD/VND. Trả về DataFrame rỗng.")
    return pd.DataFrame(columns=["date", "usdvnd"])


def fetch_sbv_rates(verbose: bool = True) -> pd.DataFrame:
    """Lãi suất điều hành SBV (tái cấp vốn, tái chiết khấu, liên ngân hàng).

    Dữ liệu lịch sử SBV không có API công khai có cấu trúc.
    Hàm này trả về bảng tĩnh các mốc thay đổi lãi suất quan trọng từ 2012 đến nay.
    Duy trì và cập nhật thủ công khi NHNN ban hành Quyết định điều chỉnh mới.
    """
    # Lịch sử điều chỉnh lãi suất điều hành của Ngân hàng Nhà nước Việt Nam (% năm)
    # Nguồn: sbv.gov.vn & các Quyết định chính sách tiền tệ NHNN
    sbv_rates = [
        # Giai đoạn 2012 - 2014: Hạ nhiệt lãi suất sau giai đoạn lạm phát 2011
        {"date": "2012-03-13", "rate_type": "refinance", "rate_pct": 14.0},
        {"date": "2012-04-11", "rate_type": "refinance", "rate_pct": 13.0},
        {"date": "2012-05-28", "rate_type": "refinance", "rate_pct": 12.0},
        {"date": "2012-06-11", "rate_type": "refinance", "rate_pct": 11.0},
        {"date": "2012-07-01", "rate_type": "refinance", "rate_pct": 10.0},
        {"date": "2012-12-24", "rate_type": "refinance", "rate_pct": 9.0},
        {"date": "2013-03-26", "rate_type": "refinance", "rate_pct": 8.0},
        {"date": "2013-05-13", "rate_type": "refinance", "rate_pct": 7.0},
        {"date": "2014-03-18", "rate_type": "refinance", "rate_pct": 6.5},
        # Giai đoạn 2017 - 2019: Ổn định và nới lỏng nhẹ
        {"date": "2017-07-10", "rate_type": "refinance", "rate_pct": 6.25},
        {"date": "2019-09-16", "rate_type": "refinance", "rate_pct": 6.0},
        # Giai đoạn 2020: Hỗ trợ nền kinh tế ứng phó Covid-19
        {"date": "2020-03-17", "rate_type": "refinance", "rate_pct": 5.0},
        {"date": "2020-05-13", "rate_type": "refinance", "rate_pct": 4.5},
        {"date": "2020-10-01", "rate_type": "refinance", "rate_pct": 4.0},
        # Giai đoạn 2022: Tăng lãi suất kiểm soát lạm phát và tỷ giá
        {"date": "2022-09-23", "rate_type": "refinance", "rate_pct": 5.0},
        {"date": "2022-10-25", "rate_type": "refinance", "rate_pct": 6.0},
        # Giai đoạn 2023: 4 lần giảm lãi suất điều hành hỗ trợ tăng trưởng
        {"date": "2023-04-03", "rate_type": "refinance", "rate_pct": 5.5},
        {"date": "2023-05-25", "rate_type": "refinance", "rate_pct": 5.0},
        {"date": "2023-06-19", "rate_type": "refinance", "rate_pct": 4.5},
        # Giai đoạn 2024 - 2026: NHNN duy trì lãi suất tái cấp vốn ổn định ở mức 4.5%/năm
        {"date": "2024-01-01", "rate_type": "refinance", "rate_pct": 4.5},
        {"date": "2024-07-01", "rate_type": "refinance", "rate_pct": 4.5},
        {"date": "2025-01-01", "rate_type": "refinance", "rate_pct": 4.5},
        {"date": "2026-01-01", "rate_type": "refinance", "rate_pct": 4.5},
    ]
    df = pd.DataFrame(sbv_rates)
    df["date"] = pd.to_datetime(df["date"])
    if verbose:
        print(f"Lãi suất SBV: {len(df)} mốc thay đổi (cần cập nhật thủ công)")
    return df


def expand_sbv_rates_daily(sbv_rates: pd.DataFrame,
                            end_date: str | None = None) -> pd.DataFrame:
    """Mở rộng bảng lãi suất SBV thành chuỗi ngày liên tục (forward-fill).

    SBV công bố lãi suất tại các mốc thay đổi, không phải hàng ngày.
    Forward-fill là phương pháp đúng: lãi suất giữ nguyên cho đến khi
    có thông báo thay đổi.
    """
    if sbv_rates.empty:
        return pd.DataFrame()
    d_end = pd.Timestamp(end_date or date.today())
    d_start = sbv_rates["date"].min()
    all_dates = pd.date_range(d_start, d_end, freq="D")
    result = pd.DataFrame({"date": all_dates})
    sbv_pivot = sbv_rates.pivot_table(
        index="date", columns="rate_type", values="rate_pct"
    ).reset_index()
    result = result.merge(sbv_pivot, on="date", how="left")
    result = result.ffill()
    return result


# ── Tương quan ────────────────────────────────────────────────────────────────

def correlation_matrix(
    spreads_df: pd.DataFrame,
    sbv_daily: pd.DataFrame | None = None,
    usdvnd_df: pd.DataFrame | None = None,
    lag_configs: list[int] | None = None,
) -> pd.DataFrame:
    """Ma trận tương quan giữa lợi suất TPCP và các chỉ số vĩ mô.

    Parameters
    ----------
    spreads_df : pd.DataFrame
        Output của spread_analysis.py (date, y_2y, y_10y, spread_10y_2y, ...)
    sbv_daily : pd.DataFrame | None
        Chuỗi lãi suất SBV hàng ngày (date, refinance)
    usdvnd_df : pd.DataFrame | None
        Tỷ giá USD/VND hàng ngày (date, usdvnd)
    lag_configs : list[int] | None
        Độ trễ (ngày) cần kiểm tra. Mặc định: [0, 5, 20, 60]

    Returns
    -------
    DataFrame ma trận tương quan và rolling correlations
    """
    if lag_configs is None:
        lag_configs = [0, 5, 20, 60]

    base = spreads_df[["date", "y_2y", "y_10y", "spread_10y_2y"]].copy()
    base["date"] = pd.to_datetime(base["date"])

    if sbv_daily is not None and len(sbv_daily):
        sbv = sbv_daily[["date", "refinance"]].copy()
        sbv["date"] = pd.to_datetime(sbv["date"])
        base = base.merge(sbv, on="date", how="left")
        # Spread TPCP - SBV: real policy rate premium
        base["premium_2y_sbv"] = base["y_2y"] - base["refinance"]
        base["premium_10y_sbv"] = base["y_10y"] - base["refinance"]

    if usdvnd_df is not None and len(usdvnd_df):
        fx = usdvnd_df[["date", "usdvnd"]].copy()
        fx["date"] = pd.to_datetime(fx["date"])
        base = base.merge(fx, on="date", how="left")
        base["usdvnd_chg_20d"] = base["usdvnd"].pct_change(20, fill_method=None) * 100

    numeric_cols = [c for c in base.columns if c != "date"]
    corr = base[numeric_cols].corr().round(3)

    return corr


def rolling_correlation(
    spreads_df: pd.DataFrame,
    secondary_series: pd.Series,
    secondary_name: str,
    window: int = 90,
) -> pd.DataFrame:
    """Rolling correlation giữa lợi suất 10Y và một chuỗi vĩ mô khác.

    Phát hiện giai đoạn tương quan/phân kỳ thay đổi theo thời gian.
    """
    base = spreads_df[["date", "y_10y"]].copy()
    base["date"] = pd.to_datetime(base["date"])
    base = base.set_index("date")

    sec = secondary_series.copy()
    sec.index = pd.to_datetime(sec.index)
    sec.name = secondary_name

    merged = base.join(sec, how="inner").dropna()
    merged[f"rolling_{window}d_corr"] = (
        merged["y_10y"].rolling(window).corr(merged[secondary_name])
    )
    return merged.reset_index()


# ── Entry point ───────────────────────────────────────────────────────────────

def run(cache_dir: str | Path = "./cache", verbose: bool = True) -> dict:
    """Chạy phân tích tương quan vĩ mô và lưu kết quả."""
    cache_dir = Path(cache_dir)

    # Nạp spread đã tính
    spread_file = cache_dir / "spread_analysis.parquet"
    if not spread_file.exists():
        if verbose:
            print("Chưa có spread_analysis.parquet. Hãy chạy spread_analysis.py trước.")
        return {}

    spreads = pd.read_parquet(spread_file)
    spreads["date"] = pd.to_datetime(spreads["date"])

    # Lãi suất SBV
    sbv_raw = fetch_sbv_rates(verbose=verbose)
    max_d_str = pd.to_datetime(spreads["date"].max()).strftime("%Y-%m-%d")
    min_d_str = pd.to_datetime(spreads["date"].min()).strftime("%Y-%m-%d")
    sbv_daily = expand_sbv_rates_daily(sbv_raw, end_date=max_d_str)

    # Tỷ giá USD/VND
    usdvnd = fetch_usdvnd_sbv(start=min_d_str, end=max_d_str, verbose=verbose)

    # Ma trận tương quan
    corr = correlation_matrix(spreads, sbv_daily, usdvnd)

    # Lưu
    corr.to_parquet(cache_dir / "macro_correlation.parquet")
    sbv_daily.to_parquet(cache_dir / "sbv_rates_daily.parquet", index=False)

    if verbose:
        print("\nMa trận tương quan (10Y yield với các yếu tố vĩ mô):")
        if "y_10y" in corr.index:
            print(corr["y_10y"].sort_values())

    return {"correlation": corr, "sbv_daily": sbv_daily, "usdvnd": usdvnd}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Tương quan TPCP với chỉ số vĩ mô")
    p.add_argument("--cache-dir", default="./cache")
    args = p.parse_args()
    run(args.cache_dir)
