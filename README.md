# AI Market Intelligence System

An automated pipeline that tracks AI-sector stocks, ingests SEC earnings filings, and delivers a daily briefing email with LLM-generated insights.

## What It Does

Every weekday morning you receive one email containing:
- **Stock summary** — open, close, high, low, % change for 8 tracked symbols
- **Alerts** — flags any stock that moved 5%+ or 10%+ in 24 hours
- **Earnings intelligence** — AI capex signals and sentiment extracted from real SEC 8-K filings via Claude
- **Trend report** — AI/GPU keyword spikes and negative sentiment detected across 800+ news headlines

## Architecture
```
Yahoo Finance  →  collector.py     →  PostgreSQL
SEC EDGAR      →  transcript_ingester.py  →  Claude API  →  PostgreSQL
Google News    →  news_fetcher.py   →  trend_detector.py   →  PostgreSQL
                                                ↓
                                         reporter.py  →  Gmail
```

## Tech Stack

- **Python** — data collection, pipeline orchestration
- **PostgreSQL** — time-series price data, earnings transcripts, news articles
- **Anthropic Claude API** — earnings summarization, sentiment detection
- **SEC EDGAR API** — real 8-K filings (EX-99.1 exhibits)
- **yfinance** — real-time stock price feeds
- **feedparser** — RSS news ingestion
- **cron** — fully automated, no manual triggers

## Automation Schedule

| Job | Schedule |
|---|---|
| Stock price collector | Every 5 min, market hours |
| News fetcher | Every 4 hours, weekdays |
| Trend detector | Every 4 hours, weekdays |
| Daily report email | 9:30am weekdays |
| Health check | Every 15 min, market hours |

## Tracked Symbols

NVDA · AMD · MSFT · GOOGL · META · AMZN · AVGO · TSM

## Setup
```bash
git clone https://github.com/Zing302/ai-market-intel
cd ai-market-intel
pip install -r requirements.txt
# Add credentials to config/.env
python setup_db.py
```

See `config/.env.example` for required environment variables.
```
