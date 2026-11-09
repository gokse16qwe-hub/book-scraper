# Book Scraper

A small, production-minded web scraper that collects product data (title, price,
rating, availability, link) from [books.toscrape.com](https://books.toscrape.com)
— a site built specifically for scraping practice — and exports it to a clean
**Excel** or **CSV** file.

> This is a portfolio project. The target is a public sandbox designed for
> scraping, so there are no terms-of-service concerns.

## Features

- **Reusable CLI** — search scope, page limit, output path and format are all
  arguments. Not a throwaway script.
- **Reliable parsing** — selectors target the site's structural classes
  (`article.product_pod`), not brittle guesses, so no row is silently dropped.
- **Pagination** — follows the "next" link to the end of the catalogue (1000
  books across 50 pages).
- **Resilient HTTP** — a `requests` session with automatic retry and backoff on
  transient errors (429/503), polite delay between pages.
- **Clean Excel output** — styled header, frozen header row, auto-filter, number
  format for price, star display for rating.
- **Proper logging** — no bare `except`; issues are logged, not swallowed.

## Why `requests` + BeautifulSoup instead of Selenium?

The target returns static HTML, so a full browser is unnecessary overhead.
`requests` is faster, lighter and less fragile here. For JavaScript-rendered
sites (infinite scroll, client-side rendering) Selenium or Playwright is the
right tool — the parsing layer in this project would stay the same.

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Scrape the whole catalogue to Excel (default)
python scraper.py --output books.xlsx

# First 5 pages only, as CSV
python scraper.py --max-pages 5 --format csv --output books.csv

# Verbose logging and a slower, politer crawl
python scraper.py --delay 1.0 --verbose
```

### Options

| Option | Short | Default | Description |
|---|---|---|---|
| `--output` | `-o` | `kitaplar.xlsx` | Output file path |
| `--format` | `-f` | from extension | `xlsx` or `csv` |
| `--max-pages` | `-p` | all | Maximum pages to crawl |
| `--delay` | `-d` | `0.5` | Seconds to wait between pages |
| `--verbose` | `-v` | off | Detailed logging |

## Sample output

`ornek_cikti.xlsx` in this repo is a real run: 1000 books, all fields populated.

## Possible extensions

- Follow each book's detail page for UPC, description and stock count.
- Add a category index so `--category travel` filters by genre.
- Swap the HTTP layer for Playwright to handle JavaScript-rendered targets.

## License

MIT
