# AI Market Intelligence

An automated pipeline that runs a multi-agent "finance firm" on 20 tracked stocks, delivering a daily briefing email with structured trade signals, sentiment analysis, and earnings intelligence.

## What It Does

Three times per market day, a four-agent pipeline runs on every tracked symbol and produces structured BUY/HOLD/SELL recommendations. At 5pm ET you receive one email with:

- **Trading signals** — approved BUY/HOLD/SELL with conviction scores, rationale, and techniques used
- **Stock summary** — open, close, high, low, % change for all tracked symbols
- **Alerts** — any stock that moved 5%+ in 24 hours
- **Trend report** — AI/sector keyword spikes detected across 800+ news headlines
- **Earnings intelligence** — AI capex signals and sentiment from SEC 8-K filings

## Architecture

```
Yahoo Finance ──────────────────────────────────────────────► stock_prices
SEC EDGAR ──► transcript_ingester ──► Claude ────────────────► summaries
Google News ──► news_fetcher ──► trend_detector ─────────────► trends

                    ┌──────────────────────────────────────────────────┐
                    │  Recommendation Pipeline  (3x/day, 20 tickers)   │
                    │                                                   │
                    │  analyst.py ──► researcher.py ──► trader.py      │
                    │  (SMA/RSI)     (Haiku sentiment)  (Haiku+tools)  │
                    │                                        │          │
                    │                                 manager.py        │
                    │                           (guards + Sonnet)       │
                    │                                        │          │
                    │                                recommendations     │
                    └──────────────────────────────────────────────────┘
                                                            │
                                                     reporter.py ──► Gmail
```

## Agents

| Agent | Model | Role |
|---|---|---|
| **Analyst** | Python / `ta` lib | Deterministic SMA(5/20) crossover and RSI(14) signals |
| **Researcher** | Claude Haiku | Qualitative sentiment across news, social posts, earnings (−1.0 to +1.0) |
| **Trader** | Claude Haiku + tool-use | Structured BUY/HOLD/SELL with schema-enforced score and rationale |
| **Manager** | Deterministic + Claude Sonnet | Guard checks → Sonnet escalation on high conviction or signal conflict |

### Trading Techniques

Four named signals the trader weighs on every call:

1. **momentum** — SMA(5) vs SMA(20) crossover direction and strength
2. **mean_reversion** — RSI(14) overbought/oversold thresholds
3. **sentiment_momentum** — researcher's 5-category sentiment score vs prior readings
4. **event_driven** — 24h/7d price spikes and news keyword surges

## Automation Schedule

| Job | Schedule (ET) |
|---|---|
| Stock price collector | Every 5 min, weekdays 9am–5pm |
| News fetcher | Every 4 hours, weekdays |
| Trend detector | 15 min after news fetch |
| Recommendation pipeline | 10:00am, 2:00pm, 4:30pm weekdays |
| Daily report email | 5:00pm weekdays |
| DB cleanup | 2:00am weekdays |
| Outcome backfill (7d) | 3:00am weekdays |

## Tracked Symbols

20 symbols across 5 sectors:

| Sector | Symbols |
|---|---|
| Tech | NVDA · AMD · MSFT · GOOGL · AVGO · TSM · AAPL · INTC |
| Energy | XOM · CVX · COP · SLB |
| Entertainment | NFLX · DIS · SPOT · RBLX |
| Finance | JPM · BAC · GS |
| Healthcare | UNH |

## Tech Stack

- **Python 3.11** — pipeline orchestration
- **PostgreSQL** — prices, articles, recommendations, agent audit log
- **Anthropic Claude API** — Haiku (researcher, trader) + Sonnet (manager escalation)
- **yfinance** — real-time stock prices
- **ta** — technical indicators (SMA, RSI)
- **feedparser** — RSS news ingestion
- **psycopg2** — database driver
- **cron** — fully automated, no manual triggers

## LLM Provider Configuration

By default, the pipeline uses Claude via Anthropic's API. You can optionally switch to a local Ollama instance by setting the `LLM_PROVIDER` environment variable.

**Supported providers:**
- `anthropic` (default) — uses Claude via Anthropic API
- `ollama` — uses a local Ollama instance

**Environment variables** (add to `config/.env`):

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | Provider to use (`anthropic` or `ollama`) |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL (used when `LLM_PROVIDER=ollama`) |
| `OLLAMA_MODEL_FAST` | `qwen3` | Ollama model name for fast calls |
| `OLLAMA_MODEL_SMART` | `qwen3` | Ollama model name for structured calls |

**To use Ollama:**

1. Ensure Ollama is running locally: `ollama serve`
2. Pull the Qwen model: `ollama pull qwen3` (or your preferred model)
3. Add to `config/.env`:
   ```
   LLM_PROVIDER=ollama
   OLLAMA_MODEL_FAST=qwen3
   OLLAMA_MODEL_SMART=qwen3
   ```
4. Run the pipeline as usual

To revert to Anthropic, simply change `LLM_PROVIDER` back to `anthropic` or remove the line (defaults to Anthropic).

## Setup

```bash
git clone https://github.com/Zing302/ai-market-intel
cd ai-market-intel
pip install -r requirements.txt
# Create config/.env and fill in required keys:
# - ANTHROPIC_API_KEY (if using Anthropic provider, the default)
# - LLM_PROVIDER, OLLAMA_HOST, OLLAMA_MODEL_FAST, OLLAMA_MODEL_SMART (if using Ollama)
# - Database credentials, Gmail app password (for email delivery)
# See "LLM Provider Configuration" section above for Ollama setup details.
python setup_db.py          # creates tables and seeds 20 tracked symbols
crontab -e                  # add entries from scripts/cron_additions.txt
```

## Project Structure

```
data/
  agents/            analyst, researcher, trader, manager
  recommendations/   orchestrator (run.py)
  collector.py       stock price collection
  news_fetcher.py    RSS article ingestion
  trend_detector.py  keyword trend detection
reports/
  reporter.py        email assembly and delivery
scripts/
  run_*.sh           cron wrappers for each job
  cleanup_db.py      per-table row retention (2–90 days)
  outcome_backfill.py  7-day recommendation scoring
utils/
  anthropic_client.py  retry + token helpers
  symbols.py           DB-backed ticker cache
  circuit_breaker.py   per-source failure isolation
tests/
  unit/              141 tests, all passing
```
