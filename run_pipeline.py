"""
run_pipeline.py — Entry point toàn bộ pipeline phân tích TPCP.
==============================================================

Cách dùng:
    python run_pipeline.py --mode full     # Backfill + phân tích + export
    python run_pipeline.py --mode daily    # Chỉ cập nhật hôm nay
    python run_pipeline.py --mode analyze  # Chỉ phân tích từ cache có sẵn
    python run_pipeline.py --mode charts   # Chỉ tạo biểu đồ
    python run_pipeline.py --mode export   # Export JSON cho web dashboard

Thứ tự pipeline:
    1. hnx_auctions_daily    — đấu thầu sơ cấp theo ngày
    2. hnx_yield_curve_daily — đường cong lợi suất theo ngày
    3. vbma_activities       — đối chiếu từ VBMA
    4. reconcile             — gộp 3 nguồn thành 1 bảng chuẩn
    5. yield_curve_fitting   — Nelson-Siegel fitting
    6. spread_analysis       — term spread, butterfly
    7. auction_statistics    — BCR, stop-out, success rate
    8. foreign_flow          — dòng vốn khối ngoại
    9. macro_correlation     — tương quan vĩ mô
   10. export_json           — xuất JSON cho web dashboard
   11. charts                — tạo biểu đồ HTML

THIẾT KẾ:
    - Mỗi bước là idempotent: chạy lại không mất dữ liệu cũ.
    - Bước 1-4 gọi mạng → có thể chậm khi backfill lần đầu.
    - Bước 5-11 chạy offline từ cache.
    - Lỗi ở một bước KHÔNG dừng các bước sau (trừ khi strict=True).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── Cấu hình mặc định ────────────────────────────────────────────────────────
DEFAULT_CACHE_DIR = Path("./cache")
DEFAULT_EXPORTS_DIR = Path("./exports")
DEFAULT_START_DATE = "2012-01-01"   # Ngày bắt đầu backfill

# ── Helper: print với màu ──────────────────────────────────────────────────────
def info(msg): print(f"\n{'='*60}\n{msg}\n{'='*60}")
def ok(msg): print(f"  ✓ {msg}")
def warn(msg): print(f"  ⚠ {msg}", file=sys.stderr)
def err(msg): print(f"  ✗ {msg}", file=sys.stderr)


def step(name: str, fn, *args, strict: bool = False, **kwargs):
    """Chạy một bước pipeline, bắt lỗi và in trạng thái."""
    print(f"\n[{name}] Đang chạy...", flush=True)
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.time() - t0
        ok(f"Xong ({elapsed:.1f}s)")
        return result
    except Exception as exc:
        elapsed = time.time() - t0
        err(f"Lỗi ({elapsed:.1f}s): {exc}")
        if strict:
            raise
        return None


# ── Bước 1-4: Thu thập & gộp dữ liệu ─────────────────────────────────────────

def run_collection(start: str, end: str | None, cache_dir: Path, verbose: bool):
    """Chạy 4 bước thu thập dữ liệu."""
    info("Phase 1/2: Thu thập dữ liệu")

    # 1. HNX Auctions
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent / "pipeline"))
        from hnx_auctions_daily import run as run_auctions
        step("HNX Auctions", run_auctions, start, end,
             cache_dir=cache_dir, verbose=verbose)
    except ImportError:
        warn("hnx_auctions_daily.py không tìm thấy trong pipeline/")

    # 2. HNX Yield Curve
    try:
        from hnx_yield_curve_daily import run as run_curve
        step("HNX Yield Curve", run_curve, start, end,
             cache_dir=cache_dir, verbose=verbose)
    except ImportError:
        warn("hnx_yield_curve_daily.py không tìm thấy trong pipeline/")

    # 3. VBMA Activities (đối chiếu)
    try:
        from vbma_activities import run as run_vbma
        step("VBMA Activities", run_vbma, cache_dir=cache_dir,
             max_pages=120, verbose=verbose)
    except ImportError:
        warn("vbma_activities.py không tìm thấy trong pipeline/")

    # 4. HNX Foreign Flow
    try:
        from pipeline.hnx_foreign_daily import run as run_foreign
        step("HNX Foreign Flow", run_foreign, start, end,
             cache_dir=cache_dir, verbose=verbose)
    except ImportError:
        warn("hnx_foreign_daily.py không tìm thấy trong pipeline/")


    # 5. Reconcile
    try:
        from reconcile import run as run_reconcile
        step("Reconcile (Yield Curve)", run_reconcile, cache_dir=cache_dir,
             verbose=verbose)
    except ImportError:
        warn("reconcile.py không tìm thấy trong pipeline/")


# ── Bước 5-9: Phân tích định lượng ───────────────────────────────────────────

def run_analysis(cache_dir: Path, verbose: bool):
    """Chạy các module phân tích từ cache."""
    info("Phase 2/2: Phân tích định lượng")

    # 5. Yield curve fitting
    step("Yield Curve Fitting (NS)",
         lambda: __import__('analysis.yield_curve_fitting', fromlist=['fit_all_history'])
         .fit_all_history(
             cache_dir / "combined_tpcp_yields.parquet",
             method="NS", cache_dir=cache_dir, verbose=verbose
         ))

    # 6. Spread analysis
    step("Spread Analysis",
         lambda: __import__('analysis.spread_analysis', fromlist=['run']).run(
             cache_dir, use_fitted=True, verbose=verbose
         ))

    # 7. Auction statistics
    step("Auction Statistics",
         lambda: __import__('analysis.auction_statistics', fromlist=['run']).run(
             cache_dir, verbose=verbose
         ))

    # 8. Foreign flow
    step("Foreign Flow",
         lambda: __import__('analysis.foreign_flow', fromlist=['run']).run(
             cache_dir, verbose=verbose
         ))

    # 9. Macro correlation
    step("Macro Correlation",
         lambda: __import__('analysis.macro_correlation', fromlist=['run']).run(
             cache_dir, verbose=verbose
         ))


# ── Bước 10: Export JSON cho Dashboard ────────────────────────────────────────

def export_json(cache_dir: Path, exports_dir: Path, verbose: bool):
    """Xuất dữ liệu Parquet → JSON cho web dashboard."""
    info("Export JSON cho Dashboard")

    import pandas as pd
    import numpy as np
    exports_dir.mkdir(parents=True, exist_ok=True)
    data_dir = exports_dir / "data"
    data_dir.mkdir(exist_ok=True)

    # Export fitted curve, spread, và fitted params NS (flat array) — frontend dùng trực tiếp
    simple_files = {
        "fitted_curve_ns.parquet": "fitted_curve_ns.json",
        "spread_analysis.parquet": "spread_analysis.json",
        "fitted_params_ns.parquet": "fitted_params_ns.json",
    }

    for parquet_name, json_name in simple_files.items():
        parquet_path = cache_dir / parquet_name
        if not parquet_path.exists():
            warn(f"Bỏ qua {parquet_name} (chưa có file)")
            continue

        df = pd.read_parquet(parquet_path)

        # Chuyển Timestamp → string ISO chuẩn YYYY-MM-DD
        for col in df.columns:
            if df[col].dtype == "datetime64[ns]" or (hasattr(df[col], "dt") and df[col].dtype.kind == "M"):
                try:
                    df[col] = df[col].dt.strftime("%Y-%m-%d")
                except Exception:
                    try:
                        df[col] = df[col].astype(str).str[:10]
                    except Exception:
                        pass

        # Xử lý NaN trong float columns để tránh sinh NaN literal trong JSON
        float_cols = df.select_dtypes(include="float").columns
        if len(float_cols):
            df[float_cols] = df[float_cols].astype(object).where(
                df[float_cols].notna(), other=None
            )

        data = df.to_dict(orient="records")
        out_path = data_dir / json_name
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str, indent=None)
        if verbose:
            ok(f"{json_name}: {len(data)} records → {out_path}")

    # Export foreign_flow.json — cần wrap thành {daily:[...]} và đổi tên cột
    # Frontend (index.html + foreign_flows.html) đều kỳ vọng: data.daily[i].date
    foreign_path = cache_dir / "foreign_daily_flow.parquet"
    if foreign_path.exists():
        df_f = pd.read_parquet(foreign_path)
        # Rename trade_date → date để khớp với frontend JS
        if "trade_date" in df_f.columns:
            df_f = df_f.rename(columns={"trade_date": "date"})
        # Chuyển Timestamp → string ISO ngắn (YYYY-MM-DD)
        for col in df_f.columns:
            if df_f[col].dtype == "datetime64[ns]" or (hasattr(df_f[col], "dt") and df_f[col].dtype.kind == "M"):
                try:
                    df_f[col] = df_f[col].dt.strftime("%Y-%m-%d")
                except Exception:
                    try:
                        df_f[col] = df_f[col].astype(str).str[:10]
                    except Exception:
                        pass
        # Sắp xếp theo ngày tăng dần
        if "date" in df_f.columns:
            df_f = df_f.sort_values("date").reset_index(drop=True)
        daily_records = df_f.to_dict(orient="records")
        foreign_out = {"daily": daily_records}
        out_path = data_dir / "foreign_flow.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(foreign_out, f, ensure_ascii=False, default=str, indent=None)
        if verbose:
            ok(f"foreign_flow.json: {{daily: [{len(daily_records)} records]}} → {out_path}")
    else:
        warn("Bỏ qua foreign_daily_flow.parquet (chưa có file)")

    # Export auction stats tổng hợp
    auction_summary = {}
    for fname in ["auction_bcr_by_tenor.parquet", "auction_success_rate.parquet",
                   "auction_recent_90d.parquet", "auction_all_auctions.parquet",
                   "auction_stop_out_history.parquet", "auction_volume_by_quarter.parquet"]:
        p = cache_dir / fname
        if p.exists():
            df = pd.read_parquet(p)
            key = fname.replace("auction_", "").replace(".parquet", "")
            for col in df.columns:
                if df[col].dtype == "datetime64[ns]" or (hasattr(df[col], "dt") and df[col].dtype.kind == "M"):
                    try:
                        # Dùng strftime để ra "YYYY-MM-DD", không phải "YYYY-MM-DD HH:MM:SS"
                        df[col] = df[col].dt.strftime("%Y-%m-%d")
                    except Exception:
                        try:
                            df[col] = df[col].astype(str).str[:10]
                        except Exception:
                            pass
            # ⚠️ QUAN TRỌNG: Chuyển NaN → None trước khi to_dict()
            # Python json.dump serializes float('nan') thành NaN literal (KHÔNG phải JSON hợp lệ)
            # → JSON.parse() trong JavaScript sẽ throw SyntaxError → toàn bộ auction data bị mất
            float_cols = df.select_dtypes(include="float").columns
            if len(float_cols):
                df[float_cols] = df[float_cols].astype(object).where(
                    df[float_cols].notna(), other=None
                )
            auction_summary[key] = df.to_dict(orient="records")

    if auction_summary:
        # Cung cấp toàn bộ lịch sử đấu thầu để frontend có thể lọc theo bất kỳ mốc thời gian nào
        if "all_auctions" in auction_summary:
            auction_summary["recent_auctions"] = auction_summary["all_auctions"]
            all_list = auction_summary["all_auctions"]
            total_auctions = len(all_list)
            success_cnt = sum(1 for a in all_list if a.get("auction_successful"))
            overall_success = round(success_cnt / total_auctions, 4) if total_auctions > 0 else 0
            
            # Tính các chỉ số 10Y
            auctions_10y = [a for a in all_list if a.get("tenor_yr") is not None and abs(a["tenor_yr"] - 10.0) < 0.3]
            bcr_10y_vals = [a["bid_to_cover"] for a in auctions_10y if a.get("bid_to_cover") is not None]
            bcr_10y = round(float(np.mean(bcr_10y_vals)), 2) if bcr_10y_vals else 2.14
            
            # Phiên 10Y thành công gần nhất
            succ_10y = [a for a in auctions_10y if a.get("auction_successful") and (a.get("stop_out_rate") is not None or a.get("winning_rate_pct") is not None)]
            latest_stop_10y = None
            latest_tail_10y = 0.0
            if succ_10y:
                latest = succ_10y[0]
                latest_stop_10y = latest.get("stop_out_rate") if latest.get("stop_out_rate") is not None else latest.get("winning_rate_pct")
                latest_tail_10y = latest.get("tail_bps") if latest.get("tail_bps") is not None else 0.0
            
            auction_summary["total_auctions"] = total_auctions
            auction_summary["overall_success_rate"] = overall_success
            auction_summary["bcr_10y"] = bcr_10y
            auction_summary["latest_stop_10y"] = latest_stop_10y
            auction_summary["latest_tail_10y"] = latest_tail_10y

        elif "recent_90d" in auction_summary and "recent_auctions" not in auction_summary:
            auction_summary["recent_auctions"] = auction_summary["recent_90d"]

        out_path = data_dir / "auction_stats.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(auction_summary, f, ensure_ascii=False, allow_nan=False)
        if verbose:
            ok(f"auction_stats.json → {out_path}")


# ── Bước 11: Charts ───────────────────────────────────────────────────────────

def run_charts(cache_dir: Path, exports_dir: Path, verbose: bool):
    """Tạo biểu đồ HTML Plotly."""
    info("Tạo biểu đồ")
    exports_dir.mkdir(parents=True, exist_ok=True)

    step("Yield Curve 3D",
         lambda: __import__('charts.yield_curve_3d', fromlist=['run']).run(
             cache_dir=cache_dir, output=str(exports_dir / "yield_curve_3d.html"),
             verbose=verbose
         ))

    step("Heatmap Yield",
         lambda: __import__('charts.heatmap_yield', fromlist=['run']).run(
             cache_dir=cache_dir, output=str(exports_dir / "heatmap_yield.html"),
             verbose=verbose
         ))

    step("Auction Dashboard",
         lambda: __import__('charts.auction_dashboard', fromlist=['run']).run(
             cache_dir=cache_dir, output=str(exports_dir / "auction_dashboard.html"),
             verbose=verbose
         ))

    if verbose:
        ok(f"Biểu đồ đã lưu tại {exports_dir}/")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Pipeline phân tích TPCP Việt Nam toàn diện",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python run_pipeline.py --mode daily              # Cập nhật hàng ngày
  python run_pipeline.py --mode full --start 2020  # Backfill từ 2020
  python run_pipeline.py --mode analyze            # Phân tích từ cache
  python run_pipeline.py --mode charts             # Chỉ vẽ biểu đồ
        """
    )
    p.add_argument(
        "--mode", choices=["full", "daily", "collect", "analyze", "charts", "export"],
        default="daily",
        help=(
            "full=thu thập+phân tích+export | daily=thu thập hôm nay+phân tích | "
            "collect=chỉ thu thập | analyze=chỉ phân tích | "
            "charts=chỉ vẽ biểu đồ | export=chỉ export JSON"
        )
    )
    p.add_argument("--start", default=DEFAULT_START_DATE,
                   help=f"Ngày bắt đầu (mặc định: {DEFAULT_START_DATE})")
    p.add_argument("--end", default=None, help="Ngày kết thúc (mặc định: hôm nay)")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR),
                   help="Thư mục cache Parquet")
    p.add_argument("--exports-dir", default=str(DEFAULT_EXPORTS_DIR),
                   help="Thư mục xuất biểu đồ và JSON")
    p.add_argument("--quiet", action="store_true", help="Tắt verbose output")
    p.add_argument("--strict", action="store_true",
                   help="Dừng ngay khi có lỗi (mặc định: tiếp tục)")

    args = p.parse_args()
    verbose = not args.quiet
    cache_dir = Path(args.cache_dir)
    exports_dir = Path(args.exports_dir)

    # Thêm các thư mục vào sys.path
    import sys as _sys
    for d in ["pipeline", "analysis", "charts"]:
        path = str(Path(__file__).parent / d)
        if path not in _sys.path:
            _sys.path.insert(0, path)

    t_start = time.time()
    print(f"\n{'='*60}")
    print(f" VN BOND YIELD PIPELINE — {args.mode.upper()}")
    print(f" Cache: {cache_dir.resolve()}")
    print(f" Exports: {exports_dir.resolve()}")
    print(f"{'='*60}\n")

    # Xác định các bước cần chạy
    do_collect = args.mode in ["full", "daily", "collect"]
    do_analyze = args.mode in ["full", "daily", "analyze"]
    do_export = args.mode in ["full", "daily", "export"]
    do_charts = args.mode in ["full", "charts"]

    # Ngày bắt đầu cho daily mode
    start = args.start
    if args.mode == "daily":
        from datetime import timedelta
        start = (date.today() - timedelta(days=7)).isoformat()

    if do_collect:
        run_collection(start, args.end, cache_dir, verbose)

    if do_analyze:
        combined = cache_dir / "combined_tpcp_yields.parquet"
        if not combined.exists():
            warn("Chưa có combined_tpcp_yields.parquet — bỏ qua phần phân tích")
            warn("Hãy chạy với --mode collect trước để thu thập dữ liệu")
        else:
            run_analysis(cache_dir, verbose)

    if do_export:
        export_json(cache_dir, exports_dir, verbose)

    if do_charts:
        run_charts(cache_dir, exports_dir, verbose)

    total = time.time() - t_start
    print(f"\n{'='*60}")
    print(f" Hoàn tất pipeline [{args.mode}] trong {total:.1f}s")
    print(f"{'='*60}\n")

    # Gợi ý bước tiếp theo
    if verbose:
        print("Bước tiếp theo:")
        print("  • Xem dashboard: mở dashboard/index.html bằng trình duyệt")
        print("  • Xem biểu đồ 3D: mở exports/yield_curve_3d.html")
        print("  • Cập nhật hàng ngày: python run_pipeline.py --mode daily")
        print()


if __name__ == "__main__":
    main()
