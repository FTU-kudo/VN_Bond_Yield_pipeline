"""
common.py — Tiện ích dùng chung cho toàn bộ pipeline TPCP.
=============================================================

Module này được import bởi:
    hnx_auctions_daily.py   — parse bảng HTML, số kiểu VN
    hnx_yield_curve_daily.py — vòng lặp quét hàng ngày, bảng HTML
    vbma_activities.py       — bảng HTML, bỏ dấu tiếng Việt
    reconcile.py             — số kiểu VN, bỏ dấu tiếng Việt

THIẾT KẾ:
    - Không import từ bất kỳ module pipeline nào khác (tránh vòng tròn).
    - Tất cả hàm đều thuần Python, không phụ thuộc config, dễ unit test.
    - Mọi parse số đều có fallback NaN thay vì raise — nguồn dữ liệu thô
      không bao giờ đảm bảo format nhất quán 100%.

BỐN CÁI BẪY ĐÃ BIẾT (ghi lại để không tái phạm):
    1. Số kiểu VN: dấu chấm = nghìn, dấu phẩy = thập phân.
       "1.234.567" = 1,234,567 — KHÔNG phải 1.234567.
    2. Dấu tiếng Việt trong so khớp cột: 'ngay' in 'ngày' = False.
       Luôn bỏ dấu trước khi so sánh.
    3. Phân trang HNX: total_pages luôn trả 1 (không dùng được).
       Đặt pRecordOnPage thật lớn để lấy hết 1 lần.
    4. Ngày tháng <= 12: pandas mặc định hiểu kiểu Mỹ (MM/DD/YYYY).
       Luôn dùng format="%d/%m/%Y" tường minh.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

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

import pandas as pd

# ── Bỏ dấu tiếng Việt ────────────────────────────────────────────────────────

def strip_diacritics(s: str) -> str:
    """Bỏ dấu tiếng Việt và chuẩn hoá Unicode.

    Ví dụ: 'Lãi suất trúng thầu' → 'Lai suat trung thau'
    Dùng để so khớp tên cột không phân biệt dấu.
    """
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ── Parse số kiểu Việt Nam ────────────────────────────────────────────────────

def vn_number(s) -> float:
    """Parse số kiểu Việt Nam → float.

    File dữ liệu HNX trộn HAI định dạng tuỳ thời kỳ nên phải tự nhận dạng:
        Cũ (≤ 2025-12-05):  "1.234.567"  (nghìn=chấm) · "4,0003" (thập phân=phẩy)
        Mới (≥ 2025-12-08): "1234567"    (trơn)        · "2.35"   (thập phân=chấm)

    Quy tắc phân biệt:
        - Có dấu phẩy  →  kiểu VN (chấm ngăn nghìn, phẩy thập phân).
        - Không phẩy, có chấm  →  coi là nghìn chỉ khi MỌI nhóm sau chấm
          đầu tiên đúng 3 chữ số ("2.000.000"); ngược lại là thập phân ("2.35").

    Trả về NaN nếu không parse được.
    """
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return float("nan")
    t = str(s).strip().replace("\u00a0", "").replace(" ", "")
    if not t or set(t) <= {"-", "—", "–"}:
        return float("nan")
    if "," in t:                                       # kiểu VN cũ
        t = t.replace(".", "").replace(",", ".")
    elif "." in t:
        groups = t.split(".")
        if len(groups) > 1 and all(len(g) == 3 for g in groups[1:]):
            t = t.replace(".", "")                     # dấu ngăn nghìn
        # else: giữ nguyên — dấu thập phân
    try:
        return float(t)
    except ValueError:
        return float("nan")


# ── Parse bảng HTML ──────────────────────────────────────────────────────────

_TAG = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&#(\d+);")


def _unescape(s: str) -> str:
    """Unescape HTML entities và tags."""
    import html as _html
    return _html.unescape(_TAG.sub("", s)).replace("\xa0", " ")


def _cells(row_html: str) -> list[str]:
    """Trích các ô <td>/<th> từ HTML một hàng → list[str] đã làm sạch."""
    out = []
    for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, re.S | re.I):
        t = _unescape(c)
        out.append(" ".join(t.split()))
    return out


def parse_table(html: str, table_id: str = "_tableDatas") -> tuple[list[str], list[list[str]]]:
    """Trích bảng HTML theo id → (header, rows).

    Dùng cho endpoint HNX có id="_tableDatas". Nếu không tìm thấy trả về
    ([], []).
    """
    pattern = rf'<table id="{re.escape(table_id)}".*?</table>'
    m = re.search(pattern, html, re.S | re.I)
    if not m:
        return [], []
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", m.group(0), re.S | re.I)
    if not rows:
        return [], []
    head = _cells(rows[0])
    body = [c for r in rows[1:] if (c := _cells(r)) and any(x for x in c)]
    return head, body


def extract_all_tables(html: str) -> list[list[list[str]]]:
    """Trích TẤT CẢ bảng HTML trong trang → list[bảng], mỗi bảng là list[hàng].

    Khác với parse_table() dùng cho endpoint HNX cố định id, hàm này dùng
    cho các trang web tổng quát (VBMA, HNX yield curve) có thể có nhiều bảng
    hoặc không có id cố định.

    Kết quả: mỗi phần tử là một bảng, mỗi bảng là list các hàng,
    mỗi hàng là list[str] các ô đã làm sạch. Hàng đầu là header.
    """
    tables = []
    for tbl_html in re.findall(r"<table\b[^>]*>.*?</table>", html, re.S | re.I):
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", tbl_html, re.S | re.I)
        parsed = [c for r in rows if (c := _cells(r))]
        if parsed:
            tables.append(parsed)
    return tables


# ── Vòng lặp quét hàng ngày ──────────────────────────────────────────────────

def daily_scan(
    fetch_fn: Callable[[date, object], pd.DataFrame],
    *,
    start: date,
    end: date,
    sess,
    cache_dir: Path,
    data_file: str,
    checked_file: str,
    dedup_keys: list[str],
    date_col: str,
    save_every: int = 20,
    verbose: bool = True,
) -> pd.DataFrame:
    """Vòng lặp quét hàng ngày với cache tăng dần.

    Thiết kế giải quyết ba vấn đề thực tế:
    1. Ngày rỗng (không có dữ liệu) KHÁC với ngày chưa quét:
       → Dùng checked_file riêng để ghi nhận cả ngày đã quét nhưng trống.
    2. Ngắt giữa chừng mất tiến độ nếu chỉ ghi cuối:
       → Ghi tăng dần mỗi save_every ngày xử lý.
    3. Lỗi mạng một ngày không nên dừng toàn bộ:
       → Catch exception, không ghi ngày đó vào checked → tự thử lại lần sau.

    Parameters
    ----------
    fetch_fn : Callable[[date, session], pd.DataFrame]
        Hàm fetch dữ liệu cho 1 ngày. Trả DataFrame rỗng nếu không có data.
    start, end : date
        Khoảng ngày cần quét.
    sess : requests.Session
        Session HTTP tái sử dụng (đã có throttle ở tầng vnbond.http).
    cache_dir : Path
        Thư mục lưu cache.
    data_file : str
        Tên file Parquet chứa dữ liệu.
    checked_file : str
        Tên file Parquet ghi các ngày đã quét.
    dedup_keys : list[str]
        Khoá khử trùng khi gộp dữ liệu mới vào cũ.
    date_col : str
        Tên cột ngày dùng để sort.
    save_every : int
        Ghi xuống đĩa sau mỗi bao nhiêu ngày xử lý.
    verbose : bool
        In tiến độ ra stdout.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    data_path = cache_dir / data_file
    checked_path = cache_dir / checked_file

    # Nạp trạng thái cache
    already_checked: set[date] = (
        set(pd.read_parquet(checked_path)["checked_date"].tolist())
        if checked_path.exists() else set()
    )

    pending_data: list[pd.DataFrame] = []
    pending_checked: list[date] = []

    d = start
    while d <= end:
        # Bỏ qua cuối tuần và ngày đã quét
        if d.weekday() >= 5 or d in already_checked:
            d += timedelta(days=1)
            continue

        try:
            df_day = fetch_fn(d, sess)
            if len(df_day):
                pending_data.append(df_day)
                pending_checked.append(d)
                if verbose:
                    print(f"  {d} → {len(df_day)} dòng")
            elif (date.today() - d).days > 5:
                # Chỉ đánh dấu đã kiểm tra nếu ngày đó đã qua hơn 5 ngày (ngày nghỉ/lễ cố định)
                pending_checked.append(d)
                if verbose:
                    print(f"  {d} → 0 dòng (ngày nghỉ/lễ)")
            else:
                # Ngày gần đây (<= 5 ngày) chưa có dữ liệu: Không ghi checked để lần quét sau tự động thử lại khi HNX upload PDF
                if verbose:
                    print(f"  {d} → 0 dòng (chưa xuất bản, sẽ tự quét lại lần sau)")
        except Exception as exc:  # noqa: BLE001
            # Không đánh dấu đã quét → sẽ thử lại lần sau
            if verbose:
                print(f"  {d} LỖI {type(exc).__name__}: {exc} (sẽ thử lại)")

        # Ghi tăng dần
        if len(pending_checked) >= save_every:
            _flush(
                data_path, checked_path,
                pending_data, pending_checked, already_checked,
                dedup_keys, date_col, verbose,
            )
            already_checked |= set(pending_checked)
            pending_data, pending_checked = [], []

        d += timedelta(days=1)

    # Ghi phần còn lại
    _flush(
        data_path, checked_path,
        pending_data, pending_checked, already_checked,
        dedup_keys, date_col, verbose,
    )

    return pd.read_parquet(data_path) if data_path.exists() else pd.DataFrame()


def _flush(
    data_path: Path,
    checked_path: Path,
    pending_data: list[pd.DataFrame],
    pending_checked: list[date],
    already_checked: set[date],
    dedup_keys: list[str],
    date_col: str,
    verbose: bool,
) -> None:
    """Gộp dữ liệu pending vào file trên đĩa."""
    if pending_data:
        old = pd.read_parquet(data_path) if data_path.exists() else pd.DataFrame()
        merged = pd.concat(
            [p for p in [old, *pending_data] if len(p)], ignore_index=True
        )
        # Khử trùng chỉ khi có đủ khoá
        valid_keys = [k for k in dedup_keys if k in merged.columns]
        if valid_keys:
            merged = merged.drop_duplicates(subset=valid_keys, keep="last")
        if date_col in merged.columns:
            merged = merged.sort_values(date_col).reset_index(drop=True)
        merged.to_parquet(data_path, index=False)

    if pending_checked:
        all_checked = sorted(already_checked | set(pending_checked))
        pd.DataFrame({"checked_date": all_checked}).to_parquet(checked_path, index=False)

    if verbose and pending_checked:
        n = len(pd.read_parquet(data_path)) if data_path.exists() else 0
        print(f"  --- ghi xuống đĩa: {n} dòng tổng, {len(pending_checked)} ngày mới ---")


# ── Tiện ích ngày tháng ──────────────────────────────────────────────────────

def as_date(x) -> date:
    """Chuyển str/datetime/date → date an toàn."""
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return datetime.strptime(str(x)[:10], "%Y-%m-%d").date()


def vn_weekdays(start: date, end: date) -> list[date]:
    """Danh sách ngày làm việc (thứ 2–6) trong khoảng [start, end]."""
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out
