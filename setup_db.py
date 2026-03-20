from utils.db import get_connection

CREATE_STOCK_PRICES = """
CREATE TABLE IF NOT EXISTS stock_prices (
    id         SERIAL PRIMARY KEY,
    symbol     VARCHAR(10) NOT NULL,
    price      NUMERIC(10, 2) NOT NULL,
    volume     BIGINT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_STOCK_PRICES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_prices_symbol_time
    ON stock_prices (symbol, fetched_at DESC);
"""

CREATE_ALERTS = """
CREATE TABLE IF NOT EXISTS alerts (
    id                 SERIAL PRIMARY KEY,
    symbol             VARCHAR(10) NOT NULL,
    alert_type         VARCHAR(20) NOT NULL,
    price_at_alert     NUMERIC(10, 2),
    change_pct         NUMERIC(6, 2),
    triggered_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    included_in_report BOOLEAN DEFAULT FALSE
);
"""


CREATE_TRANSCRIPTS = """
CREATE TABLE IF NOT EXISTS transcripts (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(10) NOT NULL,
    filing_date     DATE NOT NULL,
    quarter         VARCHAR(10),
    raw_text        TEXT NOT NULL,
    word_count      INT,
    source_url      VARCHAR(500),
    ingested_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, filing_date)
);
"""

CREATE_SUMMARIES = """
CREATE TABLE IF NOT EXISTS summaries (
    id                SERIAL PRIMARY KEY,
    transcript_id     INT REFERENCES transcripts(id),
    summary_text      TEXT NOT NULL,
    ai_capex_flag     BOOLEAN DEFAULT FALSE,
    prompt_tokens     INT,
    completion_tokens INT,
    model_used        VARCHAR(50),
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
"""


CREATE_ARTICLES = """
CREATE TABLE IF NOT EXISTS articles (
    id           SERIAL PRIMARY KEY,
    symbol       VARCHAR(10),
    headline     TEXT NOT NULL,
    url          TEXT UNIQUE NOT NULL,
    source       VARCHAR(50),
    published_at TIMESTAMPTZ,
    fetched_at   TIMESTAMPTZ DEFAULT NOW(),
    full_text    TEXT
);
"""

CREATE_TRENDS = """
CREATE TABLE IF NOT EXISTS trends (
    id               SERIAL PRIMARY KEY,
    symbol           VARCHAR(10),
    trend_type       VARCHAR(50),
    headline_count   INTEGER,
    sample_headlines TEXT,
    detected_at      TIMESTAMPTZ DEFAULT NOW(),
    alert_sent       BOOLEAN DEFAULT FALSE
);
"""


def setup():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_STOCK_PRICES)
            cur.execute(CREATE_STOCK_PRICES_INDEX)
            cur.execute(CREATE_ALERTS)
            cur.execute(CREATE_TRANSCRIPTS)
            cur.execute(CREATE_SUMMARIES)
            cur.execute(CREATE_ARTICLES)
            cur.execute(CREATE_TRENDS)
        conn.commit()
        print("Database tables created successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    setup()
