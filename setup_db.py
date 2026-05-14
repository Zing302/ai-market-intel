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


# ── v2 tables (additive — existing tables above are never modified) ────────────

CREATE_TRACKED_SYMBOLS = """
CREATE TABLE IF NOT EXISTS tracked_symbols (
    symbol   VARCHAR(10) PRIMARY KEY,
    sector   VARCHAR(40) NOT NULL,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    active   BOOLEAN     DEFAULT TRUE
);
"""

CREATE_SENTIMENT_SCORES = """
CREATE TABLE IF NOT EXISTS sentiment_scores (
    id                 SERIAL PRIMARY KEY,
    symbol             VARCHAR(10) NOT NULL,
    score              NUMERIC(4, 3) NOT NULL,
    category           VARCHAR(20)   NOT NULL,
    source             VARCHAR(20)   NOT NULL,
    article_count      INT           DEFAULT 0,
    social_post_count  INT           DEFAULT 0,
    transcript_used    BOOLEAN       DEFAULT FALSE,
    computed_at        TIMESTAMPTZ   DEFAULT NOW()
);
"""

CREATE_SENTIMENT_SCORES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_time
    ON sentiment_scores (symbol, computed_at DESC);
"""

CREATE_RECOMMENDATIONS = """
CREATE TABLE IF NOT EXISTS recommendations (
    id                SERIAL PRIMARY KEY,
    run_id            UUID          NOT NULL,
    symbol            VARCHAR(10)   NOT NULL,
    action            VARCHAR(10)   NOT NULL,
    score             NUMERIC(4, 3),
    rationale         TEXT,
    techniques_used   TEXT,
    price_at_rec      NUMERIC(10, 2),
    outcome_pct_7d    NUMERIC(6, 2),
    outcome_scored_at TIMESTAMPTZ,
    status            VARCHAR(20)   NOT NULL DEFAULT 'pending',
    flag_reason       TEXT,
    manager_decision  VARCHAR(20),
    confidence_flag   VARCHAR(10),
    dedup_window      VARCHAR(20)   NOT NULL,
    included_in_report BOOLEAN      DEFAULT FALSE,
    created_at        TIMESTAMPTZ   DEFAULT NOW()
);
"""

CREATE_RECOMMENDATIONS_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_recs_symbol_time
    ON recommendations (symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_recs_pending_outcome
    ON recommendations (created_at)
    WHERE outcome_scored_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_recs_dedup
    ON recommendations (symbol, dedup_window);
"""

CREATE_AGENT_RUNS = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id                SERIAL PRIMARY KEY,
    run_id            UUID          NOT NULL,
    symbol            VARCHAR(10),
    agent_role        VARCHAR(20)   NOT NULL,
    model             VARCHAR(40)   NOT NULL,
    prompt_tokens     INT,
    completion_tokens INT,
    duration_ms       INT,
    status            VARCHAR(20)   NOT NULL,
    flag_reason       TEXT,
    raw_output        TEXT,
    created_at        TIMESTAMPTZ   DEFAULT NOW()
);
"""

CREATE_AGENT_RUNS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_agent_runs_run
    ON agent_runs (run_id);
"""

CREATE_SOCIAL_POSTS = """
CREATE TABLE IF NOT EXISTS social_posts (
    id           SERIAL PRIMARY KEY,
    platform     VARCHAR(20)  NOT NULL,
    subreddit    VARCHAR(50),
    post_id      VARCHAR(50)  NOT NULL,
    title        TEXT,
    body         TEXT,
    upvotes      INT,
    author_karma INT,
    posted_at    TIMESTAMPTZ,
    fetched_at   TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (platform, post_id)
);
"""

CREATE_SOCIAL_POST_SYMBOLS = """
CREATE TABLE IF NOT EXISTS social_post_symbols (
    post_id INT          NOT NULL REFERENCES social_posts(id) ON DELETE CASCADE,
    symbol  VARCHAR(10)  NOT NULL,
    PRIMARY KEY (post_id, symbol)
);
"""

CREATE_SOCIAL_POST_SYMBOLS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_sps_symbol
    ON social_post_symbols (symbol);
"""

CREATE_TECHNICALS_VIEW = """
CREATE OR REPLACE VIEW technicals_v AS
SELECT DISTINCT ON (symbol, (fetched_at AT TIME ZONE 'America/New_York')::date)
    symbol,
    (fetched_at AT TIME ZONE 'America/New_York')::date AS trade_date,
    price AS close
FROM stock_prices
ORDER BY
    symbol,
    (fetched_at AT TIME ZONE 'America/New_York')::date,
    fetched_at DESC;
"""

# ── seed data ──────────────────────────────────────────────────────────────────

TRACKED_SYMBOLS_SEED = [
    ("NVDA", "Tech"),
    ("AMD",  "Tech"),
    ("MSFT", "Tech"),
    ("GOOGL","Tech"),
    ("META", "Tech"),
    ("AMZN", "Tech"),
    ("TSM",  "Tech"),
    ("AVGO", "Tech"),
    ("XOM",  "Energy"),
    ("CVX",  "Energy"),
    ("OXY",  "Energy"),
    ("DIS",  "Entertainment"),
    ("NFLX", "Entertainment"),
    ("WBD",  "Entertainment"),
    ("JPM",  "Finance"),
    ("GS",   "Finance"),
    ("BAC",  "Finance"),
    ("UNH",  "Healthcare"),
    ("JNJ",  "Healthcare"),
    ("PFE",  "Healthcare"),
]


def setup():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # ── existing tables (unchanged) ───────────────────────────────────
            cur.execute(CREATE_STOCK_PRICES)
            cur.execute(CREATE_STOCK_PRICES_INDEX)
            cur.execute(CREATE_ALERTS)
            cur.execute(CREATE_TRANSCRIPTS)
            cur.execute(CREATE_SUMMARIES)
            cur.execute(CREATE_ARTICLES)
            cur.execute(CREATE_TRENDS)

            # ── v2 additions ──────────────────────────────────────────────────
            cur.execute(CREATE_TRACKED_SYMBOLS)
            cur.execute(CREATE_SENTIMENT_SCORES)
            cur.execute(CREATE_SENTIMENT_SCORES_INDEX)
            cur.execute(CREATE_RECOMMENDATIONS)
            cur.execute(CREATE_RECOMMENDATIONS_INDEXES)
            cur.execute(CREATE_AGENT_RUNS)
            cur.execute(CREATE_AGENT_RUNS_INDEX)
            cur.execute(CREATE_SOCIAL_POSTS)
            cur.execute(CREATE_SOCIAL_POST_SYMBOLS)
            cur.execute(CREATE_SOCIAL_POST_SYMBOLS_INDEX)
            cur.execute(CREATE_TECHNICALS_VIEW)

        conn.commit()
        print("Database tables created successfully.")
    finally:
        conn.close()


def seed_tracked_symbols():
    """Insert all watchlist symbols, skipping any already present."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO tracked_symbols (symbol, sector)
                VALUES (%s, %s)
                ON CONFLICT (symbol) DO NOTHING
                """,
                TRACKED_SYMBOLS_SEED,
            )
            inserted = cur.rowcount
        conn.commit()
        print(f"Seeded {inserted} symbol(s) into tracked_symbols.")
    finally:
        conn.close()


if __name__ == "__main__":
    setup()
    seed_tracked_symbols()
