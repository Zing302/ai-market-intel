import sys
import os
import re
import time
import requests
from datetime import date, timedelta
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import anthropic
from dotenv import load_dotenv
from utils.db import get_connection
from utils.logger import get_logger
from config.settings import TRACKED_STOCKS

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../config/.env"))

logger = get_logger("transcript_ingester")

MODEL = "claude-haiku-4-5-20251001"
CHARS_PER_TOKEN = 4
MAX_CHARS_PER_CHUNK = 80_000 * CHARS_PER_TOKEN  # ~320k chars ≈ 80k tokens
LOOKBACK_DAYS = 90

EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SEC_HEADERS = {"User-Agent": "MarketIntelBot sbollini06@gmail.com"}

_ticker_cik_cache: dict[str, str] = {}  # ticker → zero-padded 10-digit CIK

SYSTEM_PROMPT = """You are a financial analyst specializing in AI infrastructure investment.
Extract structured intelligence from earnings call transcripts.
Be concise and factual."""

USER_PROMPT_TEMPLATE = """Analyze this earnings call transcript and provide a structured summary.
Focus on:
1. AI_CAPEX: AI-related capital expenditure
2. GPU_DEMAND: GPU procurement and chip supply
3. DATA_CENTER: Data center expansion plans
4. GUIDANCE: Forward guidance on AI revenue
5. RISKS: Headwinds and regulatory concerns
6. SENTIMENT: bullish/cautious/mixed with one sentence reason

Transcript:
{transcript_text}"""


# ── SEC EDGAR helpers ──────────────────────────────────────────────────────────

def load_ticker_cik_map() -> dict[str, str]:
    """Fetch EDGAR's full ticker→CIK map and cache it."""
    global _ticker_cik_cache
    if _ticker_cik_cache:
        return _ticker_cik_cache
    try:
        resp = requests.get(EDGAR_TICKERS_URL, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        # Response: {0: {cik_str: "...", ticker: "...", title: "..."}, 1: {...}, ...}
        for entry in resp.json().values():
            ticker = entry.get("ticker", "").upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if ticker:
                _ticker_cik_cache[ticker] = cik
        logger.info(f"Loaded {len(_ticker_cik_cache)} ticker→CIK mappings from EDGAR.")
    except Exception as e:
        logger.warning(f"Could not load ticker→CIK map: {e}")
    return _ticker_cik_cache


def search_filings(symbol: str) -> list[dict]:
    """Return list of recent 8-K filing metadata dicts for the given ticker."""
    ticker_map = load_ticker_cik_map()
    cik = ticker_map.get(symbol.upper())
    if not cik:
        logger.warning(f"{symbol}: no CIK found in EDGAR ticker map.")
        return []

    url = EDGAR_SUBMISSIONS_URL.format(cik=cik)
    try:
        time.sleep(0.15)
        resp = requests.get(url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"{symbol}: could not fetch submissions for CIK {cik}: {e}")
        return []

    filings = data.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    accessions = filings.get("accessionNumber", [])
    file_dates = filings.get("filingDate", [])
    items_list = filings.get("items", [])  # e.g. "2.02" or "2.02,8.01"

    hits = []
    skipped = 0
    for form, adsh, file_date, items_str in zip(forms, accessions, file_dates, items_list):
        if form != "8-K":
            continue
        items = [i.strip() for i in str(items_str).split(",")]
        if "2.02" not in items:
            logger.debug(f"{symbol}: skipping {adsh} ({file_date}) — items: {items}")
            skipped += 1
            continue
        hits.append({
            "adsh": adsh,
            "file_date": file_date,
            "cik": cik,
        })

    logger.debug(f"{symbol}: first earnings hit — {hits[0] if hits else 'none'}")
    logger.info(f"{symbol}: found {len(hits)} earnings 8-K(s) with item 2.02 ({skipped} non-earnings skipped).")
    return hits


def fetch_filing_text(cik: str, accession_no: str) -> tuple[str | None, str | None]:
    cik_nodash = cik.lstrip("0")
    accession_nodash = accession_no.replace("-", "")
    index_url = f"{EDGAR_ARCHIVES}/{cik_nodash}/{accession_nodash}/{accession_no}-index.htm"
    logger.debug(f"Fetching filing index: {index_url}")

    time.sleep(0.15)
    try:
        resp = requests.get(index_url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
    except Exception as e:
        logger.warning(f"Could not fetch filing index {index_url}: {e}")
        return None, None

    # Table columns: [seq, description, filename_link, type, size_bytes]
    # Collect all .htm exhibits with their type and size, then pick best
    exhibits = []
    fallback_url = None

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        link = cells[2].find("a", href=True)
        if not link:
            continue
        href = link["href"]
        # Skip inline XBRL viewer links (/ix?doc=...)
        if "/ix?" in href:
            href = href.split("doc=")[-1]
        if not href.lower().endswith((".htm", ".html")):
            continue
        full_url = f"https://www.sec.gov{href}" if href.startswith("/") else href
        doc_type = cells[3].get_text(strip=True).upper() if len(cells) > 3 else ""
        size = int(cells[4].get_text(strip=True) or 0) if len(cells) > 4 else 0
        exhibits.append((doc_type, size, full_url))
        if fallback_url is None:
            fallback_url = full_url

    # Prefer EX-99.x exhibits; among those pick the largest by byte size
    earnings_exhibits = [(size, url) for t, size, url in exhibits if t.startswith("EX-99")]
    if earnings_exhibits:
        _, doc_url = max(earnings_exhibits)
    else:
        doc_url = fallback_url
    if not doc_url:
        logger.warning(f"No .htm exhibit found in filing index: {index_url}")
        return None, None

    logger.debug(f"Fetching exhibit: {doc_url}")
    time.sleep(0.15)
    try:
        resp = requests.get(doc_url, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
        text = BeautifulSoup(resp.content, "html.parser").get_text(separator="\n", strip=True)
        return text, doc_url
    except Exception as e:
        logger.warning(f"Could not fetch document {doc_url}: {e}")
        return None, None


def infer_quarter(period_of_report: str) -> str | None:
    try:
        period = period_of_report.replace("-", "")
        month = int(period[4:6])
        year = int(period[:4])
        quarter = (month - 1) // 3 + 1
        return f"Q{quarter} {year}"
    except Exception:
        return None


# ── Database helpers ───────────────────────────────────────────────────────────

def filing_exists(cur, symbol: str, filing_date: date) -> bool:
    cur.execute(
        "SELECT 1 FROM transcripts WHERE symbol = %s AND filing_date = %s",
        (symbol, filing_date),
    )
    return cur.fetchone() is not None


def insert_transcript(cur, symbol: str, filing_date: date, quarter: str | None,
                      raw_text: str, source_url: str | None) -> int:
    word_count = len(raw_text.split())
    cur.execute(
        """
        INSERT INTO transcripts (symbol, filing_date, quarter, raw_text, word_count, source_url)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (symbol, filing_date, quarter, raw_text, word_count, source_url),
    )
    return cur.fetchone()[0]


def insert_summary(cur, transcript_id: int, summary_text: str, ai_capex_flag: bool,
                   prompt_tokens: int, completion_tokens: int) -> None:
    cur.execute(
        """
        INSERT INTO summaries
            (transcript_id, summary_text, ai_capex_flag, prompt_tokens, completion_tokens, model_used)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (transcript_id, summary_text, ai_capex_flag, prompt_tokens, completion_tokens, MODEL),
    )


# ── Anthropic helpers ──────────────────────────────────────────────────────────

def call_claude(client: anthropic.Anthropic, transcript_text: str) -> dict:
    max_attempts = 3
    backoff_schedule = [60, 120, 240]

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": USER_PROMPT_TEMPLATE.format(transcript_text=transcript_text)}],
            )
            time.sleep(5)  # proactive throttle between successful calls
            text = next((b.text for b in response.content if b.type == "text"), "")
            return {
                "text": text,
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            }
        except anthropic.RateLimitError:
            if attempt == max_attempts:
                raise RuntimeError("Anthropic API rate limit exceeded after 3 retries.")
            wait = backoff_schedule[attempt - 1]
            logger.warning(f"Rate limited by Anthropic — waiting {wait}s before retry {attempt}/3")
            time.sleep(wait)


def summarize_transcript(client: anthropic.Anthropic, raw_text: str) -> dict:
    if len(raw_text) <= MAX_CHARS_PER_CHUNK:
        return call_claude(client, raw_text)

    chunks = [raw_text[i:i + MAX_CHARS_PER_CHUNK] for i in range(0, len(raw_text), MAX_CHARS_PER_CHUNK)]
    logger.info(f"  Transcript split into {len(chunks)} chunks.")

    total_prompt_tokens = 0
    total_completion_tokens = 0
    chunk_summaries = []

    for i, chunk in enumerate(chunks):
        logger.info(f"  Summarizing chunk {i + 1}/{len(chunks)}...")
        result = call_claude(client, chunk)
        chunk_summaries.append(result["text"])
        total_prompt_tokens += result["prompt_tokens"]
        total_completion_tokens += result["completion_tokens"]

    combined = "\n\n---\n\n".join(chunk_summaries)
    final = call_claude(client, f"Combine these partial summaries into one coherent structured summary:\n\n{combined}")
    total_prompt_tokens += final["prompt_tokens"]
    total_completion_tokens += final["completion_tokens"]

    return {
        "text": final["text"],
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
    }


def check_ai_capex_flag(summary: str) -> bool:
    """True if the AI_CAPEX section mentions specific dollar amounts."""
    match = re.search(r"AI_CAPEX:?\s*(.*?)(?=\n\d+\.|GPU_DEMAND|$)", summary, re.DOTALL | re.IGNORECASE)
    if not match:
        return False
    return bool(re.search(r"\$[\d,.]+ ?(billion|million|\bB\b|\bM\b|bn|mn)", match.group(1), re.IGNORECASE))


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    conn = get_connection()
    total_ingested = 0

    try:
        for symbol in TRACKED_STOCKS:
            logger.info(f"{symbol}: searching EDGAR for 8-K filings...")
            hits = search_filings(symbol)

            if not hits:
                logger.info(f"{symbol}: no filings found.")
                continue

            for hit in hits:
                accession_no = hit["adsh"]
                filing_date_str = hit["file_date"]
                cik = hit["cik"]

                filing_date = date.fromisoformat(filing_date_str)

                with conn.cursor() as cur:
                    if filing_exists(cur, symbol, filing_date):
                        logger.info(f"{symbol}: {filing_date} already in DB, skipping.")
                        continue

                logger.info(f"{symbol}: fetching {accession_no} ({filing_date})...")
                raw_text, source_url = fetch_filing_text(cik, accession_no)

                if not raw_text or len(raw_text.strip()) < 200:
                    logger.warning(f"{symbol}: filing text too short or empty, skipping.")
                    continue

                quarter = infer_quarter(filing_date_str)

                # Insert transcript
                try:
                    with conn.cursor() as cur:
                        transcript_id = insert_transcript(cur, symbol, filing_date, quarter, raw_text, source_url)
                    conn.commit()
                    logger.info(f"{symbol}: transcript stored (id={transcript_id}, {len(raw_text.split())} words).")
                except Exception as e:
                    conn.rollback()
                    logger.error(f"{symbol}: failed to insert transcript — {e}")
                    continue

                # Summarize and store
                try:
                    logger.info(f"{symbol}: summarizing with {MODEL}...")
                    summary = summarize_transcript(client, raw_text)
                    ai_capex_flag = check_ai_capex_flag(summary["text"])
                    with conn.cursor() as cur:
                        insert_summary(cur, transcript_id, summary["text"], ai_capex_flag,
                                       summary["prompt_tokens"], summary["completion_tokens"])
                    conn.commit()
                    logger.info(
                        f"{symbol}: summary stored — ai_capex={ai_capex_flag}, "
                        f"tokens in={summary['prompt_tokens']} out={summary['completion_tokens']}"
                    )
                    total_ingested += 1
                except Exception as e:
                    conn.rollback()
                    logger.error(f"{symbol}: summarization failed — {e}")

    finally:
        conn.close()

    logger.info(f"Ingestion complete — {total_ingested} new transcripts processed.")


if __name__ == "__main__":
    run()
