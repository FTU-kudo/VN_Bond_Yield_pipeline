"""
hnx_auctions_daily.py — Lấy kết quả đấu thầu TPCP từ HNX, TỪNG NGÀY MỘT.

TẠI SAO PHẢI THEO NGÀY (không theo tháng/năm):
    Endpoint Bond_KetQua_DauThau của HNX giới hạn cứng ~10 dòng/lần gọi,
    bất kể tham số pRecordOnPage truyền vào là bao nhiêu, và KHÔNG có phân
    trang thật — đã xác nhận thực nghiệm: gọi 12 lần riêng từng tháng của
    2015 ra đúng 120 dòng (12×10), trong khi gọi 1 lần cho cả năm chỉ ra
    10 dòng; và pCurrentPage=2 cho một tuần bận thực tế luôn trả về 0 dòng.
    Ngày bận nhất từng quan sát được chỉ có 4 mã trái phiếu, nên gọi theo
    NGÀY là mức chi tiết duy nhất đảm bảo không mất dữ liệu.

TẠI SAO TỰ VIẾT LỚP CACHE (không dùng cache tích hợp của vnbond.hnx.auctions):
    Đọc source thấy vnbond.hnx.auctions() gọi
        cache.append("hnx_auctions", df, keys=["Ngày đấu thầu", "Mã TP"])
    nhưng bảng trả về THẬT không có 2 cột tên đó — cột thật là "Ngày TCPH"
    và "Mã trái phiếu" (đã tự kiểm chứng bằng Colab). Vì cache.append() chỉ
    khử trùng khi tìm thấy cột khớp, hai tên cột sai này khiến bước khử
    trùng không bao giờ chạy. Script này tự quản lý cache với đúng tên cột
    thật để tránh bug đó.

TẠI SAO CÓ FILE "checked_dates" RIÊNG (ngoài file dữ liệu chính):
    Phần lớn ngày làm việc KHÔNG có phiên đấu thầu nào (trả về 0 dòng) — đó
    là chuyện bình thường, không phải lỗi. Nhưng nếu chỉ suy ra "ngày nào đã
    quét" từ chính dữ liệu (như bản đầu tiên của script này làm), những ngày
    0-dòng đó sẽ KHÔNG BAO GIỜ được đánh dấu là đã quét — mỗi lần chạy lại
    sẽ hỏi lại HNX y hệt các ngày rỗng đó, không có lợi gì từ cache tăng dần.
    File "checked_dates.parquet" ghi lại MỌI ngày đã thực sự gửi request
    thành công (bất kể có dữ liệu hay không), tách biệt với file dữ liệu.

TẠI SAO GHI TĂNG DẦN (không đợi quét xong hết mới ghi):
    Backfill 2012→nay mất khoảng 30-60+ phút trên Colab. Nếu chỉ ghi 1 lần
    ở cuối, mất kết nối/hết giờ giữa chừng sẽ mất sạch tiến độ. Script này
    ghi xuống đĩa sau mỗi `save_every` ngày đã xử lý — chạy lại (kể cả sau
    khi bị ngắt) sẽ tự động tiếp tục từ ngày dở dang nhờ "checked_dates".

TẦNG MẠNG: tái dùng vnbond.http (throttle 0.4s/host đã đo thực nghiệm cho
    hnx.vn, nhận diện trang chặn WAF, retry có backoff+jitter).

GIỚI HẠN ĐÃ BIẾT:
    - Lịch nghỉ lễ chỉ có các ngày cố định theo dương lịch. Tết Âm lịch
      (nghỉ ~1 tuần, ngày thay đổi mỗi năm) CHƯA được xử lý — hệ quả duy
      nhất là vài request thừa vô hại vào đúng tuần đó mỗi năm (server trả
      0 dòng vì thị trường đóng cửa, không phải lỗi, và những ngày đó vẫn
      được ghi vào checked_dates nên chỉ tốn 1 lần).
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from vnbond import http
from vnbond.sources.hnx import parse_table, _as_date

EP_PRIMARY = "https://www.hnx.vn/ModuleReportBonds/Bond_DauThau/Bond_KetQua_DauThau"
REF_PRIMARY = "https://www.hnx.vn/trai-phieu/ket-qua-dau-thau.html"

RAW_DATE_COL = "Ngày TCPH"        # cột ngày THẬT trả về từ HNX (chuỗi dd/mm/yyyy)
RAW_CODE_COL = "Mã trái phiếu"    # cột mã THẬT trả về từ HNX
DATE_COL = "trade_date"           # cột ngày đã parse an toàn, dùng để cache/dedup

DATA_FILE = "hnx_auctions_daily.parquet"
CHECKED_FILE = "hnx_auctions_checked_dates.parquet"

# Ngày lễ dương lịch cố định — xem "GIỚI HẠN ĐÃ BIẾT" ở trên về Tết Âm lịch
FIXED_HOLIDAYS_MMDD = {(1, 1), (4, 30), (5, 1), (9, 2)}


def _is_fixed_holiday(d: date) -> bool:
    return (d.month, d.day) in FIXED_HOLIDAYS_MMDD


def _vn_number(s: str) -> float:
    """'1.000.000.000' -> 1e9 ; '4,8' -> 4.8 ; '' -> NaN.
    Dấu chấm là phân cách nghìn, dấu phẩy là phân cách thập phân trong dữ
    liệu HNX — bỏ hết dấu chấm trước, rồi đổi phẩy thành chấm."""
    s = (s or "").strip()
    if not s:
        return float("nan")
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return float("nan")


def fetch_auctions_day(d: date, sess) -> pd.DataFrame:
    """Kết quả đấu thầu của MỘT ngày. Trả về DataFrame rỗng nếu không có phiên."""
    key = f"{d:%d/%m/%Y}|{d:%d/%m/%Y}|0||3|'VND'|0|0"
    r = http.post(EP_PRIMARY, session=sess, timeout=120, data={
        "p_keysearch": key, "pColOrder": "col_x", "pOrderType": "DESC",
        "pCurrentPage": 1, "pRecordOnPage": 5000, "pIsSearch": 1})
    head, rows = parse_table(r.text)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=head[: len(rows[0])])

    # QUAN TRỌNG: parse ngày với format="%d/%m/%Y" TƯỜNG MINH — không dùng
    # pd.to_datetime(..., errors="coerce") mặc định, vì với chuỗi ngày<=12
    # (vd "03/09/2026"), pandas mặc định hiểu kiểu Mỹ (tháng/ngày) và đọc
    # nhầm thành ngày 9 tháng 3 thay vì 3 tháng 9.
    if RAW_DATE_COL in df.columns:
        df[DATE_COL] = pd.to_datetime(df[RAW_DATE_COL], format="%d/%m/%Y", errors="coerce")

    # "Lãi suất trúng thầu" = "0" hoặc rỗng KHÔNG có nghĩa lãi suất thật = 0%
    # — đó là phiên thất bại hoặc không ai đặt thầu. Dùng khối lượng trúng
    # thầu thật để xác định phiên có thành công hay không.
    df["winning_rate_pct"] = df.get(
        "Lãi suất trúng thầu (%/Năm)", pd.Series(dtype=str)).map(_vn_number)
    df["winning_volume"] = df.get(
        "GT trúng thầu", pd.Series(dtype=str)).map(_vn_number)
    df["auction_successful"] = (df["winning_volume"] > 0) & df["winning_rate_pct"].notna()

    df["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    df["source"] = "hnx_auctions_daily"
    return df


def _load_cache(cache_dir: Path) -> pd.DataFrame:
    f = cache_dir / DATA_FILE
    return pd.read_parquet(f) if f.exists() else pd.DataFrame()


def _load_checked_dates(cache_dir: Path) -> set[date]:
    f = cache_dir / CHECKED_FILE
    if not f.exists():
        return set()
    return set(pd.read_parquet(f)["checked_date"].tolist())


def _flush(cache_dir: Path, pending_data: list[pd.DataFrame],
           pending_checked: list[date], verbose: bool) -> None:
    """Gộp phần vừa quét được vào file trên đĩa — gọi định kỳ, không đợi
    tới cuối, để không mất tiến độ nếu bị ngắt giữa chừng."""
    if pending_data:
        old = _load_cache(cache_dir)
        merged = pd.concat([p for p in [old, *pending_data] if len(p)], ignore_index=True)
        if DATE_COL in merged.columns and RAW_CODE_COL in merged.columns:
            merged = merged.drop_duplicates(subset=[DATE_COL, RAW_CODE_COL], keep="last")
            merged = merged.sort_values(DATE_COL).reset_index(drop=True)
        merged.to_parquet(cache_dir / DATA_FILE, index=False)

    if pending_checked:
        old_checked = _load_checked_dates(cache_dir)
        all_checked = sorted(old_checked | set(pending_checked))
        pd.DataFrame({"checked_date": all_checked}).to_parquet(
            cache_dir / CHECKED_FILE, index=False)

    if verbose:
        n_data = len(_load_cache(cache_dir))
        n_checked = len(_load_checked_dates(cache_dir))
        print(f"  --- đã ghi xuống đĩa: {n_data} dòng dữ liệu, "
              f"{n_checked} ngày đã quét ---")


def run(start: str | date, end: str | date | None = None, *,
        cache_dir: str | Path = "./cache", verbose: bool = True,
        save_every: int = 20) -> pd.DataFrame:
    """Quét theo từng ngày làm việc, bỏ qua ngày đã quét (dù có dữ liệu hay
    không), ghi tăng dần mỗi `save_every` ngày xử lý được."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    d0, d1 = _as_date(start), _as_date(end or date.today())
    already_checked = _load_checked_dates(cache_dir)
    sess = http.session(REF_PRIMARY)

    pending_data: list[pd.DataFrame] = []
    pending_checked: list[date] = []

    d = d0
    while d <= d1:
        if d.weekday() >= 5 or _is_fixed_holiday(d) or d in already_checked:
            d += timedelta(days=1)
            continue
        try:
            df_day = fetch_auctions_day(d, sess)
            if len(df_day):
                pending_data.append(df_day)
            pending_checked.append(d)   # chỉ đánh dấu đã quét khi KHÔNG lỗi
            if verbose:
                print(f"  {d} -> {len(df_day)} dòng")
        except Exception as exc:
            # Không thêm vào pending_checked -> lần chạy sau sẽ thử lại đúng ngày này
            if verbose:
                print(f"  {d} LỖI {type(exc).__name__}: {exc} (sẽ thử lại ở lần chạy sau)")

        if len(pending_checked) >= save_every:
            _flush(cache_dir, pending_data, pending_checked, verbose)
            pending_data, pending_checked = [], []

        d += timedelta(days=1)

    _flush(cache_dir, pending_data, pending_checked, verbose)
    return _load_cache(cache_dir)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Lấy kết quả đấu thầu TPCP từ HNX theo ngày")
    p.add_argument("start", help="Ngày bắt đầu, vd 2012-01-01")
    p.add_argument("end", nargs="?", default=None, help="Ngày kết thúc, mặc định hôm nay")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--save-every", type=int, default=20)
    a = p.parse_args()
    run(a.start, a.end, cache_dir=a.cache_dir, save_every=a.save_every)
