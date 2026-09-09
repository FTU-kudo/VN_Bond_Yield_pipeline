"""
auction_statistics.py — Phân tích thống kê kết quả đấu thầu sơ cấp TPCP.
==========================================================================

Các chỉ báo:
    1. Bid-to-cover ratio (BCR)
       Tỷ lệ GT đặt thầu / GT phát hành. BCR cao → nhu cầu mạnh từ thị trường.
       BCR < 1 → phiên thất bại hoặc phát hành vượt nhu cầu.

    2. Stop-out rate (lãi suất trúng thầu cao nhất)
       Khác với "lãi suất bình quân trúng thầu" — stop-out rate cho thấy
       mức lãi suất biên mà KBNN chấp nhận.

    3. Tail (đuôi phân phối)
       = Stop-out rate - Weighted average rate
       Tail lớn → nhà đầu tư đặt thầu phân tán, nhu cầu không đồng nhất.

    4. Auction success rate
       Tỷ lệ phiên có GT trúng thầu > 0 theo tenor và theo quý.

    5. Issuance calendar
       Khối lượng phát hành thực tế vs kế hoạch theo tenor, quý, năm.

Nguồn: hnx_auctions_daily.parquet (output của hnx_auctions_daily.py)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


# ── Tên cột thật từ HNX (đã xác nhận thực nghiệm) ───────────────────────────
COL_DATE = "trade_date"
COL_CODE = "Mã trái phiếu"
COL_TENOR = "Kỳ hạn"
COL_ISSUER = "TCPH"
COL_PLAN_VOL = "GT gọi thầu"          # Khối lượng kế hoạch (tỷ đồng)
COL_BID_VOL = "GT đặt thầu"          # Khối lượng đặt thầu
COL_WIN_VOL = "GT trúng thầu"        # Khối lượng trúng thầu
COL_WIN_RATE = "Lãi suất trúng thầu (%/Năm)"
COL_STOP_RATE = "Lãi suất trúng thầu cao nhất (%/Năm)"  # Stop-out rate
COL_SUCCESS = "auction_successful"
COL_WIN_RATE_PARSED = "winning_rate_pct"
COL_WIN_VOL_PARSED = "winning_volume"


def load_auctions(cache_dir: str | Path = "./cache") -> pd.DataFrame:
    """Nạp dữ liệu đấu thầu từ cache."""
    f = Path(cache_dir) / "hnx_auctions_daily.parquet"
    if not f.exists():
        raise FileNotFoundError(
            f"Không tìm thấy {f}. Hãy chạy hnx_auctions_daily.py trước."
        )
    df = pd.read_parquet(f)
    if COL_DATE in df.columns:
        df[COL_DATE] = pd.to_datetime(df[COL_DATE])
    return df


def _parse_vn_number(s) -> float:
    """Parse số kiểu VN → float, trả NaN nếu lỗi."""
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s or "").strip().replace("\xa0", "").replace(" ", "")
    if not t:
        return float("nan")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif "." in t:
        parts = t.split(".")
        if len(parts) > 1 and all(len(g) == 3 for g in parts[1:]):
            t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return float("nan")


# ── Chuẩn bị DataFrame ────────────────────────────────────────────────────────

def prepare_auction_df(df: pd.DataFrame) -> pd.DataFrame:
    """Chuẩn hoá và tính các cột phân tích."""
    df = df.copy()

    # Parse số nếu chưa parse
    for col, target in [
        (COL_WIN_RATE, COL_WIN_RATE_PARSED),
        (COL_WIN_VOL, COL_WIN_VOL_PARSED),
        (COL_PLAN_VOL, "plan_volume"),
        (COL_BID_VOL, "bid_volume"),
    ]:
        if col in df.columns and target not in df.columns:
            df[target] = df[col].map(_parse_vn_number)
        elif target not in df.columns:
            df[target] = float("nan")

    # Bid-to-cover ratio
    df["bid_to_cover"] = df["bid_volume"] / df[COL_WIN_VOL_PARSED].replace(0, float("nan"))

    # Stop-out rate
    if COL_STOP_RATE in df.columns:
        df["stop_out_rate"] = df[COL_STOP_RATE].map(_parse_vn_number)
        df["tail_bps"] = (df["stop_out_rate"] - df[COL_WIN_RATE_PARSED]) * 100
    else:
        df["stop_out_rate"] = float("nan")
        df["tail_bps"] = float("nan")

    # Cờ thành công
    if COL_SUCCESS not in df.columns:
        df[COL_SUCCESS] = (
            df[COL_WIN_VOL_PARSED].fillna(0) > 0
        ) & df[COL_WIN_RATE_PARSED].notna()

    # Tenor chuẩn hoá
    if COL_TENOR in df.columns:
        from analysis.yield_curve_fitting import tenor_to_years
        df["tenor_yr"] = df[COL_TENOR].map(tenor_to_years)

    # Kỳ (quý)
    if COL_DATE in df.columns:
        df["year"] = df[COL_DATE].dt.year
        df["quarter"] = df[COL_DATE].dt.to_period("Q").astype(str)

    return df


# ── Thống kê tổng hợp ─────────────────────────────────────────────────────────

def bid_to_cover_summary(df: pd.DataFrame) -> pd.DataFrame:
    """BCR trung bình theo tenor và theo quý.

    Chỉ tính cho các phiên thành công (có người trúng thầu).
    """
    ok = df[df[COL_SUCCESS] == True].copy()  # noqa: E712
    if ok.empty:
        return pd.DataFrame()

    by_tenor = (
        ok.groupby("tenor_yr")["bid_to_cover"]
        .agg(["mean", "median", "std", "count"])
        .round(2)
        .rename(columns={"mean": "bcr_mean", "median": "bcr_median",
                          "std": "bcr_std", "count": "n_auctions"})
        .reset_index()
    )
    return by_tenor


def stop_out_rate_history(df: pd.DataFrame,
                           tenor_yr_filter: float | None = None) -> pd.DataFrame:
    """Lịch sử stop-out rate theo tenor.

    Parameters
    ----------
    tenor_yr_filter : float | None
        Lọc theo tenor (năm). None = tất cả tenor.
    """
    ok = df[df[COL_SUCCESS] == True].copy()  # noqa: E712
    if tenor_yr_filter is not None:
        ok = ok[np.abs(ok["tenor_yr"].fillna(-1) - tenor_yr_filter) < 0.3]
    return (
        ok[[COL_DATE, "tenor_yr", COL_WIN_RATE_PARSED, "stop_out_rate", "tail_bps"]]
        .dropna(subset=[COL_DATE, COL_WIN_RATE_PARSED])
        .sort_values(COL_DATE)
        .reset_index(drop=True)
    )


def auction_success_rate(df: pd.DataFrame) -> pd.DataFrame:
    """Tỷ lệ phiên thành công theo tenor và theo quý."""
    if COL_SUCCESS not in df.columns:
        return pd.DataFrame()

    by_q = (
        df.groupby(["quarter", "tenor_yr"])[COL_SUCCESS]
        .agg(success_rate="mean", n_total="count", n_success="sum")
        .reset_index()
    )
    by_q["success_rate"] = by_q["success_rate"].round(3)
    return by_q


def issuance_volume_by_quarter(df: pd.DataFrame) -> pd.DataFrame:
    """Khối lượng phát hành thực tế (GT trúng thầu) theo quý và tenor."""
    ok = df[df[COL_SUCCESS] == True].copy()  # noqa: E712
    if ok.empty:
        return pd.DataFrame()
    grouped = (
        ok.groupby(["quarter", "tenor_yr"])[COL_WIN_VOL_PARSED]
        .sum()
        .reset_index()
        .rename(columns={COL_WIN_VOL_PARSED: "win_volume_bn"})
    )
    grouped["win_volume_bn"] = grouped["win_volume_bn"].round(1)
    return grouped


def recent_auctions_summary(df: pd.DataFrame, n_days: int = 90) -> pd.DataFrame:
    """Tóm tắt các phiên đấu thầu trong 90 ngày gần nhất."""
    if COL_DATE not in df.columns:
        return pd.DataFrame()
    cutoff = df[COL_DATE].max() - pd.Timedelta(days=n_days)
    recent = df[df[COL_DATE] >= cutoff].copy()
    cols = [COL_DATE, "tenor_yr", COL_WIN_RATE_PARSED, "bid_to_cover",
            "stop_out_rate", "tail_bps", COL_SUCCESS, COL_WIN_VOL_PARSED]
    return (
        recent[[c for c in cols if c in recent.columns]]
        .sort_values(COL_DATE, ascending=False)
        .reset_index(drop=True)
    )


# ── Phân tích theo kỳ hạn ─────────────────────────────────────────────────────

def tenor_yield_trend(df: pd.DataFrame,
                       tenor_yr: float,
                       window: int = 90) -> pd.DataFrame:
    """Xu hướng lãi suất trúng thầu theo tenor, có rolling average.

    Parameters
    ----------
    tenor_yr : float
        Kỳ hạn (năm), ví dụ 10.0 = 10 năm
    window : int
        Cửa sổ rolling average (ngày dương lịch, không phải ngày làm việc)
    """
    ok = df[df[COL_SUCCESS] == True].copy()  # noqa: E712
    mask = np.abs(ok["tenor_yr"].fillna(-1) - tenor_yr) < 0.3
    sub = ok[mask].sort_values(COL_DATE).reset_index(drop=True)
    if sub.empty:
        return sub

    sub = sub.set_index(COL_DATE)[[COL_WIN_RATE_PARSED, "stop_out_rate", "tail_bps"]]
    sub[f"ma{window}d"] = (
        sub[COL_WIN_RATE_PARSED].rolling(f"{window}D").mean()
    )
    return sub.reset_index()


# ── Entry point ───────────────────────────────────────────────────────────────

def run(cache_dir: str | Path = "./cache", verbose: bool = True) -> dict:
    """Tính toàn bộ thống kê đấu thầu và lưu Parquet.

    Returns
    -------
    dict với các DataFrame: bcr, success_rate, volume_by_quarter, recent
    """
    cache_dir = Path(cache_dir)
    raw = load_auctions(cache_dir)
    df = prepare_auction_df(raw)

    results = {
        "bcr_by_tenor": bid_to_cover_summary(df),
        "success_rate": auction_success_rate(df),
        "volume_by_quarter": issuance_volume_by_quarter(df),
        "recent_90d": recent_auctions_summary(df, n_days=90),
        "stop_out_10y": stop_out_rate_history(df, tenor_yr_filter=10.0),
    }

    # Lưu các bảng
    for name, result_df in results.items():
        if len(result_df):
            out_path = cache_dir / f"auction_{name}.parquet"
            result_df.to_parquet(out_path, index=False)
            if verbose:
                print(f"  {name}: {len(result_df)} dòng → {out_path.name}")

    if verbose:
        total = len(df)
        success = df[COL_SUCCESS].sum() if COL_SUCCESS in df.columns else 0
        print(f"\nTổng: {total} phiên đấu thầu, {success} thành công "
              f"({100*success/total:.1f}%)")
        date_range = f"{df[COL_DATE].min().date()} → {df[COL_DATE].max().date()}"
        print(f"Khoảng thời gian: {date_range}")

    return results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Thống kê kết quả đấu thầu TPCP")
    p.add_argument("--cache-dir", default="./cache")
    args = p.parse_args()
    run(args.cache_dir)
