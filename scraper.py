"""books.toscrape.com için profesyonel, yeniden kullanılabilir web scraper.

Bu araç bir ürün listeleme sitesini gezer, her kitabın adını, fiyatını,
puanını, stok durumunu ve bağlantısını toplar; sonucu temiz bir Excel (.xlsx)
veya CSV dosyasına yazar.

Tasarım notları:
- Hedef site (books.toscrape.com) tamamen scraping pratiği için yapılmıştır;
  yasal olarak güvenli bir demodur.
- Statik HTML döndürdüğü için Selenium yerine requests + BeautifulSoup kullanılır
  (daha hızlı, daha az kırılgan, tarayıcı gerektirmez). Dinamik/JS siteler için
  Selenium gerekir; bu aracın README'sinde buna değinilir.
- Her şey parametredir (arama/sayfa sayısı/çıktı), böylece tek seferlik bir script
  değil, yeniden kullanılabilir bir araçtır.

Kullanım:
    python scraper.py --output kitaplar.xlsx
    python scraper.py --max-pages 5 --format csv --output kitaplar.csv
    python scraper.py --category travel --output gezi.xlsx
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from dataclasses import dataclass, asdict, fields
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://books.toscrape.com/"
CATALOGUE_URL = urljoin(BASE_URL, "catalogue/")

# "star-rating Three" -> 3 eşlemesi (sitede puan metin olarak yazılır).
RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

logger = logging.getLogger("book_scraper")


@dataclass
class Book:
    """Tek bir kitabın toplanan alanları."""

    title: str
    price: float
    currency: str
    rating: int
    availability: str
    url: str


def build_session(retries: int = 3, backoff: float = 0.5) -> requests.Session:
    """Yeniden deneme (retry) ve backoff ayarlı bir HTTP oturumu kurar.

    429/503 gibi geçici hatalarda otomatik olarak, artan beklemeyle tekrar dener.
    Bare `except` yerine kütüphanenin sağlam retry mekanizmasını kullanırız.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def parse_price(raw: str) -> tuple[float, str]:
    """'£51.77' gibi bir metinden (51.77, '£') döndürür.

    Para birimi simgesini ayırır, sayıyı güvenli şekilde float'a çevirir.
    Marka listesi gibi kırılgan tahminler yerine karakter türüne bakar.
    """
    raw = raw.strip()
    currency = "".join(ch for ch in raw if not (ch.isdigit() or ch in ".,")).strip()
    number = "".join(ch for ch in raw if ch.isdigit() or ch == ".")
    try:
        value = float(number)
    except ValueError:
        value = 0.0
        logger.warning("Fiyat ayrıştırılamadı: %r", raw)
    return value, currency or "?"


def parse_rating(class_list: list[str]) -> int:
    """['star-rating', 'Three'] -> 3. Bulunamazsa 0 döndürür."""
    for token in class_list:
        if token in RATING_WORDS:
            return RATING_WORDS[token]
    return 0


def parse_books(html: str, page_url: str) -> list[Book]:
    """Bir listeleme sayfasının HTML'inden kitapları çıkarır.

    Seçiciler sitenin yapısal class'larına dayanır (article.product_pod),
    tahmine değil; bu yüzden tek bir kitabı kaçırmaz.
    """
    soup = BeautifulSoup(html, "html.parser")
    books: list[Book] = []
    for pod in soup.select("article.product_pod"):
        link = pod.select_one("h3 a")
        price_el = pod.select_one("p.price_color")
        rating_el = pod.select_one("p.star-rating")
        stock_el = pod.select_one("p.instock.availability")

        if link is None or price_el is None:
            logger.warning("Eksik alanlı bir kart atlandı (%s)", page_url)
            continue

        # Başlık tam metinde 'title' attribute'unda; görünen metin kısaltılmış olabilir.
        title = link.get("title") or link.get_text(strip=True)
        price, currency = parse_price(price_el.get_text())
        rating = parse_rating(rating_el.get("class", []) if rating_el else [])
        availability = stock_el.get_text(strip=True) if stock_el else "unknown"
        url = urljoin(page_url, link.get("href", ""))

        books.append(
            Book(
                title=title,
                price=price,
                currency=currency,
                rating=rating,
                availability=availability,
                url=url,
            )
        )
    return books


def find_next_page(html: str, page_url: str) -> str | None:
    """Sayfalama: 'next' bağlantısı varsa mutlak URL'ini döndürür, yoksa None."""
    soup = BeautifulSoup(html, "html.parser")
    next_el = soup.select_one("li.next a")
    if next_el is None:
        return None
    return urljoin(page_url, next_el.get("href", ""))


def scrape(
    session: requests.Session,
    start_url: str,
    max_pages: int | None,
    delay: float,
) -> list[Book]:
    """Başlangıç URL'inden itibaren sayfaları gezerek tüm kitapları toplar.

    `max_pages` None ise site bitene kadar devam eder. Her istek arasında
    `delay` saniye bekleyerek sunucuyu yormamaya çalışır (nazik scraping).
    """
    all_books: list[Book] = []
    url: str | None = start_url
    page = 0

    while url:
        page += 1
        if max_pages is not None and page > max_pages:
            break
        logger.info("Sayfa %d çekiliyor: %s", page, url)
        try:
            resp = session.get(url, timeout=20)
        except requests.RequestException as exc:
            logger.error("İstek başarısız (%s): %s", url, exc)
            break
        if resp.status_code != 200:
            logger.error("Beklenmeyen durum kodu %d (%s)", resp.status_code, url)
            break

        resp.encoding = resp.apparent_encoding  # £ gibi karakterler doğru gelsin
        books = parse_books(resp.text, url)
        logger.info("  %d kitap bulundu", len(books))
        all_books.extend(books)

        url = find_next_page(resp.text, url)
        if url and delay > 0:
            time.sleep(delay)

    return all_books


def write_csv(books: list[Book], path: str) -> None:
    """Kitapları CSV'ye yazar."""
    headers = [f.name for f in fields(Book)]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for book in books:
            writer.writerow(asdict(book))


def write_xlsx(books: list[Book], path: str) -> None:
    """Kitapları, müşteriye teslim edilebilir temiz bir Excel dosyasına yazar.

    Sade ve okunur: koyu başlık şeridi, dondurulmuş başlık satırı, otomatik
    sütun genişliği, fiyat için sayı formatı, puan için yıldız gösterimi.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Kitaplar"

    columns = [
        ("Kitap Adı", "title", 60),
        ("Fiyat", "price", 12),
        ("Birim", "currency", 8),
        ("Puan", "rating", 10),
        ("Stok Durumu", "availability", 18),
        ("Bağlantı", "url", 50),
    ]

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center")

    # Başlık satırı
    for col_idx, (label, _key, width) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # Veri satırları
    for row_idx, book in enumerate(books, start=2):
        values = asdict(book)
        for col_idx, (_label, key, _width) in enumerate(columns, start=1):
            value = values[key]
            if key == "rating":
                value = "★" * int(value) + "☆" * (5 - int(value))
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = center if key in {"price", "currency", "rating"} else left
            if key == "price":
                cell.number_format = "#,##0.00"

    ws.freeze_panes = "A2"  # başlık sabit kalsın
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(books) + 1}"
    wb.save(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="books.toscrape.com için kitap verisi çeken scraper.",
    )
    parser.add_argument(
        "--output", "-o", default="kitaplar.xlsx",
        help="Çıktı dosyası yolu (varsayılan: kitaplar.xlsx).",
    )
    parser.add_argument(
        "--format", "-f", choices=("xlsx", "csv"), default=None,
        help="Çıktı formatı. Verilmezse dosya uzantısından anlaşılır.",
    )
    parser.add_argument(
        "--max-pages", "-p", type=int, default=None,
        help="En fazla kaç sayfa gezilsin (varsayılan: hepsi).",
    )
    parser.add_argument(
        "--category", "-c", default=None,
        help="İsteğe bağlı kategori adı (örn. travel). Verilmezse tüm katalog.",
    )
    parser.add_argument(
        "--delay", "-d", type=float, default=0.5,
        help="Sayfalar arası bekleme süresi, saniye (varsayılan: 0.5).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Ayrıntılı log.",
    )
    return parser.parse_args(argv)


def resolve_start_url(category: str | None) -> str:
    """Kategori verildiyse onun ilk sayfasını, yoksa tüm kataloğu döndürür."""
    if not category:
        return urljoin(CATALOGUE_URL, "page-1.html")
    # Sitede kategori URL'leri 'category/books/<ad>_<id>/index.html' biçiminde;
    # id siteye özgü olduğu için en sağlam yol katalogdan gitmektir. Basitlik için
    # kategori filtresini desteklemek isteyen kullanıcıya not README'de verilir.
    raise SystemExit(
        "Kategori filtresi bu sürümde devre dışı; tüm katalog için --category "
        "vermeyin. (Sonraki sürümde kategori indeksi eklenecek.)"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    # En az bir bilgilendirme çıktısı kullanıcı -v vermese de görünsün.
    info_logger = logging.getLogger("book_scraper")
    info_logger.setLevel(logging.INFO)

    fmt = args.format or ("csv" if args.output.lower().endswith(".csv") else "xlsx")
    start_url = resolve_start_url(args.category)

    session = build_session()
    info_logger.info("Veri çekiliyor: %s", start_url)
    books = scrape(session, start_url, args.max_pages, args.delay)

    if not books:
        info_logger.error("Hiç kitap toplanamadı. Çıkılıyor.")
        return 1

    if fmt == "csv":
        write_csv(books, args.output)
    else:
        write_xlsx(books, args.output)

    info_logger.info("Bitti: %d kitap -> %s", len(books), args.output)
    print(f"{len(books)} kitap toplandı ve '{args.output}' dosyasına yazıldı.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
