"""
vbma_activities.py — Cào trang "Hoạt động gần đây" CÔNG KHAI của VBMA
(vbma.org.vn/vi/activities), dùng làm nguồn ĐỐI CHIẾU cho ngày có đấu thầu
TPCP. Không cần đăng nhập (đã xác nhận: cả người dùng và Yuanta đều không
có tài khoản thành viên VBMA nên không dùng được endpoint CSV nội bộ).

VAI TRÒ TRONG PIPELINE: đối chiếu/bổ sung — KHÔNG PHẢI nguồn chính. Nguồn
chính là hnx_auctions_daily.py (đã kiểm chứng đầy đủ, cấu trúc rõ ràng).
VBMA dùng để: (a) xác nhận HNX không bỏ sót phiên thật nào, (b) có góc
nhìn độc lập khi cần đối chiếu.

BẪY DỮ LIỆU ĐÃ XÁC NHẬN THỰC NGHIỆM: bài đăng TRƯỚC 17/06/2021 đều mang
field "ngày đăng" giống hệt nhau (17/06/2021) — dấu vết site được migrate
đúng ngày đó, KHÔNG PHẢI ngày thật của bài. Vì vậy script này KHÔNG BAO GIỜ
dùng field ngày hiển thị trên trang danh sách — luôn tự parse ngày thật từ
chuỗi "ngày dd/mm/yyyy" nằm ngay trong tiêu đề bài (mọi bài "Kết quả đấu
thầu Trái phiếu Chính phủ" đều có sẵn ngày trong tiêu đề).

CHIẾN LƯỢC PHÂN TRANG: trang không có API lọc theo khoảng ngày, chỉ phân
trang tuần tự (mới nhất ở trang 1). Quét từ trang 1 trở đi, DỪNG khi gặp
bài đã thấy ở lần chạy trước (checkpoint theo URL bài — nhanh cho cập nhật
hàng ngày). Có thêm TRẦN SỐ TRANG CỨNG `max_pages` — bài học từ vụ
CW_pipeline_daily từng treo 3 tiếng vì vòng lặp không có giới hạn — để
không bao giờ chạy vô tận nếu vì lý do gì đó checkpoint không khớp được
nữa (VD: VBMA đổi cấu trúc URL). Lần chạy ĐẦU TIÊN (backfill) cần
`max_pages` đủ lớn (hiện tại toàn trang là ~77, nên dùng vd 120 cho dư).

MỨC ĐỘ TRÍCH XUẤT SỐ LIỆU — CHƯA ĐƯỢC KIỂM CHỨNG BẰNG DỮ LIỆU SỐNG:
    Bài viết là văn xuôi, không phải bảng số có cấu trúc đồng nhất như
    HNX. Script này CHỈ trích số liệu có cấu trúc khi tìm thấy bảng HTML
    thật trong bài (`structured=True`, lưu nguyên bảng dạng JSON ở cột
    `table_json`) — nếu không thấy bảng, lưu lại văn bản đã làm sạch
    (`raw_text`, cắt 5000 ký tự đầu) với `structured=False` để đọc tay,
    KHÔNG tự đoán số liệu từ văn xuôi. Chưa từng chạy sống để xem bài thật
    có bảng HTML hay chỉ có văn xuôi — cần tự kiểm tra vài dòng đầu ra
    trước khi tin tưởng phần `structured=True`.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from common import extract_all_tables

BASE_URL = "https://vbma.org.vn/vi/activities"
ARTICLE_TITLE_RE = re.compile(
    r"K[eế]t\s*qu[aả]\s*đấu\s*thầu.*?ng[aà]y\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)

DATA_FILE = "vbma_activities.parquet"
SEEN_FILE = "vbma_seen_urls.parquet"

# VBMA chưa có số đo nhịp thực nghiệm riêng như vnbond.http (vốn đo cho
# hnx.vn/sbv.gov.vn) — tự đặt 1.0s an toàn, có thể chỉnh lại nếu VBMA chặn.
MIN_INTERVAL = 1.0
_last_call = [0.0]


def _throttle() -> None:
    wait = MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _get(url: str) -> requests.Response:
    _throttle()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5,vi;q=0.3",
        "Referer": "https://vbma.org.vn/"
    }
    r = requests.get(url, timeout=30, headers=headers)
    r.raise_for_status()
    return r


def _list_page_posts(page: int) -> list[dict]:
    """Danh sách bài trên 1 trang: [{"url":..., "title":...}, ...]."""
    r = _get(f"{BASE_URL}?page={page}")
    hrefs = re.findall(r'<a[^>]+href="(/vi/activities/[^"]+)"[^>]*>(.*?)</a>',
                       r.text, re.S | re.I)
    tag = re.compile(r"<[^>]+>")
    out, seen_here = [], set()
    for href, inner in hrefs:
        title = " ".join(tag.sub("", inner).split())
        if not title or href in seen_here:
            continue
        seen_here.add(href)
        out.append({"url": "https://vbma.org.vn" + href, "title": title})
    return out


def _extract_article(url: str) -> dict:
    """Thử tìm bảng HTML thật trong bài trước; không có thì lưu văn bản
    thô để đọc tay. XEM CẢNH BÁO Ở ĐẦU FILE — chưa kiểm chứng sống."""
    r = _get(url)
    tables = extract_all_tables(r.text)
    real_tables = [t for t in tables if len(t) >= 2 and len(t[0]) >= 2]
    if real_tables:
        best = max(real_tables, key=len)  # bảng nhiều dòng nhất trong bài
        return {"structured": True,
                "table_json": json.dumps({"header": best[0], "rows": best[1:]},
                                         ensure_ascii=False),
                "raw_text": None}
    tag = re.compile(r"<[^>]+>")
    text = " ".join(tag.sub(" ", r.text).split())
    return {"structured": False, "table_json": None, "raw_text": text[:5000]}


def _load_seen(cache_dir: Path) -> set[str]:
    f = cache_dir / SEEN_FILE
    return set(pd.read_parquet(f)["url"].tolist()) if f.exists() else set()


def run(*, cache_dir: str | Path = "./cache", max_pages: int = 100,
        verbose: bool = True) -> pd.DataFrame:
    """Quét từ trang 1, DỪNG khi gặp bài đã thấy ở lần chạy trước — có
    trần cứng `max_pages` để không bao giờ chạy vô tận."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    seen = _load_seen(cache_dir)

    new_rows: list[dict] = []
    new_seen: set[str] = set()
    hit_cap = True

    page = 1
    while page <= max_pages:
        posts = _list_page_posts(page)
        if not posts:
            if verbose:
                print(f"  trang {page}: không còn bài -> dừng (đã tới trang cuối thật)")
            hit_cap = False
            break

        stop = False
        for post in posts:
            if post["url"] in seen:
                stop = True
                break
            m = ARTICLE_TITLE_RE.search(post["title"])
            if not m:
                new_seen.add(post["url"])  # không phải bài đấu thầu, vẫn đánh dấu đã thấy
                continue
            real_date = datetime.strptime(m.group(1), "%d/%m/%Y").date()
            article = _extract_article(post["url"])
            new_rows.append({
                "date": pd.Timestamp(real_date),
                "url": post["url"],
                "title": post["title"],
                **article,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "source": "vbma_activities",
            })
            new_seen.add(post["url"])
            if verbose:
                print(f"  {real_date} <- {post['title'][:60]}")

        if verbose:
            print(f"  trang {page}: {len(posts)} bài, "
                  f"{len(new_rows)} bài kết quả đấu thầu khớp tới giờ")
        if stop:
            if verbose:
                print(f"  gặp bài đã thấy ở lần chạy trước -> dừng ở trang {page}")
            hit_cap = False
            break
        page += 1

    if hit_cap and verbose:
        print(f"  ĐÃ CHẠM TRẦN {max_pages} TRANG mà chưa gặp bài cũ hay hết trang — "
              f"dừng an toàn. Có thể checkpoint không khớp (VD: VBMA đổi cấu trúc "
              f"URL) — kiểm tra lại trước khi tăng max_pages và chạy tiếp.")

    if new_rows:
        old = pd.read_parquet(cache_dir / DATA_FILE) if (cache_dir / DATA_FILE).exists() else pd.DataFrame()
        merged = pd.concat([old, pd.DataFrame(new_rows)], ignore_index=True)
        merged = merged.drop_duplicates(subset=["url"], keep="last")
        merged = merged.sort_values("date").reset_index(drop=True)
        merged.to_parquet(cache_dir / DATA_FILE, index=False)

    pd.DataFrame({"url": sorted(seen | new_seen)}).to_parquet(cache_dir / SEEN_FILE, index=False)

    out = pd.read_parquet(cache_dir / DATA_FILE) if (cache_dir / DATA_FILE).exists() else pd.DataFrame()
    if verbose:
        print(f"  đã ghi {len(out)} bài kết quả đấu thầu vào {cache_dir / DATA_FILE}")
        if len(out):
            print(f"  trong đó structured=True: {(out['structured'] == True).sum()}")  # noqa: E712
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Cào VBMA activities, đối chiếu ngày đấu thầu TPCP")
    p.add_argument("--cache-dir", default="./cache")
    p.add_argument("--max-pages", type=int, default=100,
                    help="Lần đầu (backfill) nên đặt lớn (vd 120); các lần sau mặc định là đủ")
    a = p.parse_args()
    run(cache_dir=a.cache_dir, max_pages=a.max_pages)
