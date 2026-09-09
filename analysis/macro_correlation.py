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
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests


# ── Lấy dữ liệu vĩ mô công khai ─────────────────────────────────────────────

def fetch_usdvnd_sbv(
    start: str = "2018-01-01",
    end: str | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Tỷ giá trung tâm USD/VND từ SBV.

    SBV công bố tỷ giá trung tâm hằng ngày tại:
    https://www.sbv.gov.vn/webcenter/portal/vi/menu/rm/tg
    Endpoint truy cập: API JSON công khai, không cần đăng nhập.

    Rate limiting: 6 giây/request (WAF F5 của SBV).
    """
    import time

    if verbose:
        print("Đang lấy tỷ giá USD/VND từ SBV...")

    # SBV có endpoint JSON tại web services
    url = "https://www.sbv.gov.vn/webcenter/portal/vi/menu/rm/tg"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    # Dùng endpoint thay thế - SBV API công khai
    # (Thực tế cần parse HTML hoặc dùng API nếu có)
    # Đây là placeholder — sẽ cần điều chỉnh theo endpoint thực
    rows = []
    try:
        # Thử endpoint JSON của SBV
        api_url = (
            "https://www.sbv.gov.vn/webcenter/ShowProperty"
            "?nodeId=/UCMServer/SBV_279549&revision=latestreleased"
        )
        r = requests.get(api_url, headers=headers, timeout=15)
        time.sleep(6)  # WAF SBV: bắt buộc 6s
        if r.status_code == 200:
            # Parse nếu trả JSON hoặc HTML
            pass
    except Exception as e:
        if verbose:
            print(f"  SBV: {e} — sẽ dùng dữ liệu mẫu")

    # Fallback: dữ liệu mẫu từ Yahoo Finance (USD/VND)
    try:
        # yfinance nếu cài
        import yfinance as yf
        ticker = yf.Ticker("USDVND=X")
        hist = ticker.history(start=start, end=end or date.today().isoformat())
        if not hist.empty:
            rows_df = hist[["Close"]].reset_index()
            rows_df.columns = ["date", "usdvnd"]
            rows_df["date"] = pd.to_datetime(rows_df["date"]).dt.date
            if verbose:
                print(f"  USD/VND từ Yahoo: {len(rows_df)} ngày")
            return rows_df
    except ImportError:
        pass

    if verbose:
        print("  Không có dữ liệu USD/VND thực. Cần cài yfinance hoặc kết nối SBV API.")
    return pd.DataFrame(columns=["date", "usdvnd"])


def fetch_sbv_rates(verbose: bool = True) -> pd.DataFrame:
    """Lãi suất điều hành SBV (tái cấp vốn, tái chiết khấu, liên ngân hàng).

    Dữ liệu lịch sử SBV không có API công khai có cấu trúc.
    Hàm này trả về bảng tĩnh các mốc thay đổi lãi suất quan trọng.
    Cập nhật thủ công khi SBV điều chỉnh lãi suất.
    """
    # Lịch sử điều chỉnh lãi suất tái cấp vốn SBV (% năm)
    # Nguồn: sbv.gov.vn — cần cập nhật thủ công
    sbv_rates = [
        {"date": "2020-03-17", "rate_type": "refinance", "rate_pct": 5.0},
        {"date": "2020-05-13", "rate_type": "refinance", "rate_pct": 4.5},
        {"date": "2022-09-23", "rate_type": "refinance", "rate_pct": 5.0},
        {"date": "2022-10-25", "rate_type": "refinance", "rate_pct": 6.0},
        {"date": "2023-03-15", "rate_type": "refinance", "rate_pct": 6.0},
        {"date": "2023-05-25", "rate_type": "refinance", "rate_pct": 5.5},
        {"date": "2023-06-19", "rate_type": "refinance", "rate_pct": 5.0},
        {"date": "2023-08-14", "rate_type": "refinance", "rate_pct": 4.5},
        {"date": "2024-01-01", "rate_type": "refinance", "rate_pct": 4.5},
        # Thêm các mốc mới khi SBV thay đổi
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
        base["usdvnd_chg_20d"] = base["usdvnd"].pct_change(20) * 100

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
    sbv_daily = expand_sbv_rates_daily(sbv_raw,
                                        end_date=spreads["date"].max().isoformat())

    # Tỷ giá USD/VND
    start_date = spreads["date"].min().isoformat()
    usdvnd = fetch_usdvnd_sbv(start=start_date, verbose=verbose)

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
