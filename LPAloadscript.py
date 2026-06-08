# ============================================================
# Louisville Planting Guide App
# Initial PostgreSQL Load Script
# Requires: psycopg2-binary, python-dotenv
# Install:  pip install psycopg2-binary python-dotenv
# Developed with and refined using Claude 4.6 Sonnet (Anthropic, 2026),
# Google Colaboratory and Gemini 2.5 Flash (2026)
# ============================================================


# ============================================================
# IMPORTS
# ------------------------------------------------------------
# psycopg2       — PostgreSQL adapter for Python
# execute_values — efficient bulk INSERT helper from psycopg2
# load_dotenv    — loads environment variables from a .env file
# os             — reads environment variables at runtime
# sys            — used to exit with an error code on failure
# ============================================================

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
import os
import sys

load_dotenv()


# ============================================================
# DATABASE CONFIGURATION
# ------------------------------------------------------------
# Reads connection credentials from environment variables set
# in a .env file. Create a .env file in the same directory as
# this script with the following keys:
#
#   DB_HOST=db.your-project.supabase.co
#   DB_PORT=5432
#   DB_NAME=postgres
#   DB_USER=postgres
#   DB_PASSWORD=your_password_here
#   DB_SSL=require
#
# Add .env to your .gitignore so it is never committed to
# version control.
# ============================================================

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     os.getenv("DB_PORT",     "5432"),
    "dbname":   os.getenv("DB_NAME",     "postgres"),
    "user":     os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "sslmode":  os.getenv("DB_SSL",      "require"),
}


def get_connection_string() -> str:
    """
    Builds a psycopg2-compatible DSN (Data Source Name) string
    from DB_CONFIG. Used as the argument to psycopg2.connect().
    """
    return (
        f"host={DB_CONFIG['host']} "
        f"port={DB_CONFIG['port']} "
        f"dbname={DB_CONFIG['dbname']} "
        f"user={DB_CONFIG['user']} "
        f"password={DB_CONFIG['password']} "
        f"sslmode={DB_CONFIG['sslmode']}"
    )


# ============================================================
# DDL — TABLE DEFINITIONS
# ------------------------------------------------------------
# DDL (Data Definition Language) statements define the
# structure of the database. These strings are executed
# against PostgreSQL to drop and recreate all four tables.
#
# Drop order matters: child tables (those with foreign keys)
# must be dropped before the parent tables they reference.
# Create order is the reverse: parents first, then children.
#
# Drop order:   risk → temps → plants → category
# Create order: category → plants → temps → risk
# ============================================================

DROP_TABLES = """
DROP TABLE IF EXISTS risk     CASCADE;
DROP TABLE IF EXISTS temps    CASCADE;
DROP TABLE IF EXISTS plants   CASCADE;
DROP TABLE IF EXISTS category CASCADE;
"""

# category: root lookup table with no foreign key dependencies.
# Stores the three plant categories with descriptions for the UI.
CREATE_CATEGORY = """
CREATE TABLE category (
    category_id     SERIAL       PRIMARY KEY,
    category_name   TEXT         NOT NULL UNIQUE
                                 CHECK (category_name IN ('vegetable/fruit', 'herb', 'flower')),
    category_desc   TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  category               IS 'Lookup table for plant categories. One row per category.';
COMMENT ON COLUMN category.category_id   IS 'Surrogate primary key.';
COMMENT ON COLUMN category.category_name IS 'Category label: vegetable/fruit, herb, or flower.';
COMMENT ON COLUMN category.category_desc IS 'Human-readable description shown in the UI.';
"""

# plants: static reference table loaded from the soil temperature
# spreadsheet. Stores per-plant temperature thresholds for seed
# germination at 6cm depth only. Loaded once at setup; does not
# change at runtime.
CREATE_PLANTS = """
CREATE TABLE plants (
    plant_id            SERIAL   PRIMARY KEY,
    common_name         TEXT     NOT NULL,
    category_id         INTEGER  NOT NULL
                                 REFERENCES category (category_id)
                                 ON DELETE RESTRICT,
    min_soil_temp_6cm   FLOAT    NOT NULL CHECK (min_soil_temp_6cm >= 0),
    opt_soil_temp_6cm   FLOAT    NOT NULL CHECK (opt_soil_temp_6cm >= min_soil_temp_6cm),
    min_air_temp        FLOAT    NOT NULL,
    opt_air_temp        FLOAT    NOT NULL CHECK (opt_air_temp >= min_air_temp),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  plants                   IS 'Static plant reference data. One row per plant.';
COMMENT ON COLUMN plants.plant_id          IS 'Surrogate primary key.';
COMMENT ON COLUMN plants.common_name       IS 'Human-readable plant name (e.g. tomato, basil).';
COMMENT ON COLUMN plants.category_id       IS 'FK to category table.';
COMMENT ON COLUMN plants.min_soil_temp_6cm IS 'Minimum soil temp in F at 6cm for seed germination.';
COMMENT ON COLUMN plants.opt_soil_temp_6cm IS 'Optimal soil temp in F at 6cm for seed germination.';
COMMENT ON COLUMN plants.min_air_temp      IS 'Minimum air temp in F required for safe planting.';
COMMENT ON COLUMN plants.opt_air_temp      IS 'Optimal air temp in F for best results.';
CREATE INDEX idx_plants_category_id ON plants (category_id);
"""

# temps: time-series table populated at runtime from the Open
# Meteo API. Stores one row per hour covering the past 7 days
# (actual readings) and next 7 days (forecast). timestamp is
# the natural primary key since each hour is unique per location.
CREATE_TEMPS = """
CREATE TABLE temps (
    timestamp       TIMESTAMPTZ  PRIMARY KEY,
    is_forecast     BOOLEAN      NOT NULL,
    air_temp        INTEGER      NOT NULL,
    soil_6cm_temp   INTEGER      NOT NULL,
    fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  temps               IS 'Hourly air and soil temperature readings from Open Meteo API.';
COMMENT ON COLUMN temps.timestamp     IS 'Datetime of the reading (America/New_York, stored as UTC). Natural primary key.';
COMMENT ON COLUMN temps.is_forecast   IS 'TRUE = predicted future reading; FALSE = actual past reading.';
COMMENT ON COLUMN temps.air_temp      IS 'Air temperature in Fahrenheit at 2m height.';
COMMENT ON COLUMN temps.soil_6cm_temp IS 'Soil temperature in Fahrenheit at 6cm depth for seed germination.';
COMMENT ON COLUMN temps.fetched_at    IS 'When this row was inserted from the API.';
CREATE INDEX idx_temps_timestamp ON temps (timestamp DESC);
"""

# risk: derived results table. Computed by the risk engine each
# time the app runs. One row per plant storing the summarized
# 14-day temperature window and resulting risk tier for seed
# germination based on 6cm soil temperatures.
CREATE_RISK = """
CREATE TABLE risk (
    risk_id             SERIAL       PRIMARY KEY,
    plant_id            INTEGER      NOT NULL
                                     REFERENCES plants (plant_id)
                                     ON DELETE CASCADE,
    risk_time           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    risk_level          TEXT         NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    risk_desc           TEXT,
    min_14day_air       INTEGER      NOT NULL,
    min_14day_soil6cm   INTEGER      NOT NULL,
    window_start        TIMESTAMPTZ  NOT NULL,
    window_end          TIMESTAMPTZ  NOT NULL
);
COMMENT ON TABLE  risk                   IS 'Seed germination risk assessment computed per plant per app run.';
COMMENT ON COLUMN risk.risk_id           IS 'Surrogate primary key.';
COMMENT ON COLUMN risk.plant_id          IS 'FK to plants table.';
COMMENT ON COLUMN risk.risk_time         IS 'Timestamp when this assessment was computed.';
COMMENT ON COLUMN risk.risk_level        IS 'Resulting risk tier: low, medium, or high.';
COMMENT ON COLUMN risk.risk_desc         IS 'Human-readable explanation of the risk level assigned.';
COMMENT ON COLUMN risk.min_14day_air     IS 'Lowest air temp in F across the 14-day window.';
COMMENT ON COLUMN risk.min_14day_soil6cm IS 'Lowest soil temp in F at 6cm across the 14-day window.';
COMMENT ON COLUMN risk.window_start      IS 'Start of the 14-day temperature window used in this assessment.';
COMMENT ON COLUMN risk.window_end        IS 'End of the 14-day temperature window used in this assessment.';
CREATE INDEX idx_risk_plant_id  ON risk (plant_id);
CREATE INDEX idx_risk_risk_time ON risk (risk_time DESC);
"""


# ============================================================
# SEED DATA
# ------------------------------------------------------------
# Static data loaded once at setup. Categories are seeded first
# since plants references them via category_id.
#
# PLANTS tuples follow this column order:
#   (common_name, category_name,
#    min_soil_6cm, opt_soil_6cm,
#    min_air, opt_air)
#
# Temperature values are in Fahrenheit.
# Sources:
#   https://www.backwoodsenergy.org/seed-germination-temperature-chart.html
#   https://www.almanac.com/soil-temperature-chart
# ============================================================

CATEGORIES = [
    ("vegetable/fruit", "Edible crops including vegetables, fruiting plants, and berries grown for harvest."),
    ("herb",            "Aromatic or culinary plants grown for their leaves, seeds, or flavor."),
    ("flower",          "Ornamental flowering plants grown for aesthetics or pollinator support."),
]

PLANTS = [
    # (common_name, category, min_soil_6cm, opt_soil_6cm, min_air, opt_air)
    # Vegetables / fruit
    ("asparagus",       "vegetable/fruit",  50, 77,   40, 75),
    ("bean",            "vegetable/fruit",  60, 85,   50, 80),
    ("beet",            "vegetable/fruit",  40, 85,   40, 75),
    ("blackberry",      "vegetable/fruit",  45, 75,   40, 75),
    ("cabbage",         "vegetable/fruit",  45, 85,   40, 75),
    ("carrot",          "vegetable/fruit",  45, 85,   40, 75),
    ("celery",          "vegetable/fruit",  60, 70,   50, 75),
    ("chard",           "vegetable/fruit",  50, 85,   40, 75),
    ("collard",         "vegetable/fruit",  45, 85,   40, 75),
    ("cucumber",        "vegetable/fruit",  60, 95,   60, 85),
    ("eggplant",        "vegetable/fruit",  60, 95,   60, 85),
    ("gourds",          "vegetable/fruit",  70, 95,   60, 85),
    ("ground cherry",   "vegetable/fruit",  65, 85,   55, 80),
    ("leek",            "vegetable/fruit",  50, 77,   40, 75),
    ("lettuce",         "vegetable/fruit",  35, 75,   40, 70),
    ("melon",           "vegetable/fruit",  70, 95,   60, 85),
    ("okra",            "vegetable/fruit",  65, 95,   60, 90),
    ("onion",           "vegetable/fruit",  35, 85,   40, 75),
    ("parsnip",         "vegetable/fruit",  35, 70,   40, 70),
    ("sweet pea",       "vegetable/fruit",  40, 75,   40, 70),
    ("southern pea",    "vegetable/fruit",  60, 95,   55, 85),
    ("pepper",          "vegetable/fruit",  65, 95,   60, 85),
    ("pumpkin",         "vegetable/fruit",  60, 95,   60, 85),
    ("radish",          "vegetable/fruit",  40, 90,   40, 75),
    ("sorghum",         "vegetable/fruit",  60, 95,   60, 90),
    ("spinach",         "vegetable/fruit",  35, 75,   40, 65),
    ("squash",          "vegetable/fruit",  60, 95,   60, 85),
    ("strawberry",      "vegetable/fruit",  50, 80,   40, 75),
    ("sweet corn",      "vegetable/fruit",  50, 95,   55, 85),
    ("tomatillo",       "vegetable/fruit",  65, 85,   55, 80),
    ("tomato",          "vegetable/fruit",  60, 85,   55, 80),
    ("turnip",          "vegetable/fruit",  40, 85,   40, 75),
    # Flowers
    ("cosmos",          "flower",           65, 85,   55, 80),
    ("marigold",        "flower",           65, 85,   55, 80),
    ("senna",           "flower",           65, 85,   55, 80),
    ("sunflower",       "flower",           55, 85,   50, 80),
    ("zinnia",          "flower",           70, 85,   60, 85),
    # Herbs
    ("basil",           "herb",             65, 85,   60, 80),
    ("chives",          "herb",             50, 85,   40, 75),
    ("cilantro",        "herb",             55, 75,   40, 70),
    ("dill",            "herb",             60, 70,   45, 70),
    ("mint",            "herb",             55, 70,   45, 70),
    ("mustard",         "herb",             40, 75,   40, 70),
    ("oregano",         "herb",             65, 85,   55, 80),
    ("parsley",         "herb",             50, 85,   40, 75),
    ("sage",            "herb",             60, 85,   50, 80),
    ("thyme",           "herb",             60, 85,   50, 80),
]


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def run_ddl(cur, sql: str, label: str) -> None:
    """
    Executes a single DDL statement and prints a status line.
    Called once per table during the create phase.
    """
    print(f"  {label}... ", end="", flush=True)
    cur.execute(sql)
    print("done")


def verify(cur) -> None:
    """
    Runs three post-load checks and prints results to the console:
      1. Row counts per table
      2. Plant counts grouped by category
      3. Constraint check — confirms no optimal temp is below minimum
    """
    print("\nVerification")
    print("-" * 40)

    # Check 1: row counts per table
    cur.execute("""
        SELECT table_name, row_count FROM (
            SELECT 'category' AS table_name, COUNT(*) AS row_count FROM category
            UNION ALL
            SELECT 'plants',  COUNT(*) FROM plants
            UNION ALL
            SELECT 'temps',   COUNT(*) FROM temps
            UNION ALL
            SELECT 'risk',    COUNT(*) FROM risk
        ) t
    """)
    print(f"{'Table':<12} {'Rows':>6}")
    for row in cur.fetchall():
        print(f"  {row[0]:<10} {row[1]:>6}")

    # Check 2: plant counts per category
    cur.execute("""
        SELECT c.category_name, COUNT(p.plant_id) AS plant_count
        FROM category c
        LEFT JOIN plants p ON p.category_id = c.category_id
        GROUP BY c.category_name
        ORDER BY c.category_name
    """)
    print(f"\n{'Category':<20} {'Plants':>6}")
    for row in cur.fetchall():
        print(f"  {row[0]:<18} {row[1]:>6}")

    # Check 3: confirm no optimal temp is below its minimum
    cur.execute("""
        SELECT common_name FROM plants
        WHERE opt_soil_temp_6cm < min_soil_temp_6cm
           OR opt_air_temp < min_air_temp
    """)
    bad = cur.fetchall()
    if bad:
        print(f"\nWARNING: constraint violations found: {[r[0] for r in bad]}")
    else:
        print("\nAll temperature constraints passed.")


# ============================================================
# MAIN
# ------------------------------------------------------------
# Orchestrates the full load sequence:
#   1. Connect to the database
#   2. Drop existing tables
#   3. Create tables in dependency order
#   4. Seed categories, then plants
#   5. Commit and verify
#
# All DDL and seed inserts run inside a single transaction.
# If anything fails, the whole transaction is rolled back so
# the database is never left in a partially loaded state.
# ============================================================

def main() -> None:
    conn = None
    cur = None

    # -- Step 1: connect ------------------------------------------
    print("Connecting to database...")
    try:
        conn = psycopg2.connect(get_connection_string())
        conn.autocommit = False
        cur = conn.cursor()
        print("  Connected.")
    except psycopg2.OperationalError as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    try:
        # -- Step 2: drop existing tables -------------------------
        print("\nDropping existing tables...")
        cur.execute(DROP_TABLES)
        print("  Done.")

        # -- Step 3: create tables --------------------------------
        print("\nCreating tables...")
        ddl_statements = [
            (CREATE_CATEGORY, "category"),
            (CREATE_PLANTS,   "plants"),
            (CREATE_TEMPS,    "temps"),
            (CREATE_RISK,     "risk"),
        ]
        for sql_statement, label in ddl_statements:
            try:
                run_ddl(cur, sql_statement, label)
            except psycopg2.Error as e:
                print(f"\nERROR: Failed to create table '{label}': {e}")
                conn.rollback()
                sys.exit(1)

        # -- Step 4a: seed categories -----------------------------
        print("\nSeeding categories...")
        execute_values(
            cur,
            "INSERT INTO category (category_name, category_desc) VALUES %s",
            CATEGORIES
        )
        print(f"  Inserted {len(CATEGORIES)} categories.")

        # -- Step 4b: seed plants ---------------------------------
        print("\nSeeding plants...")
        cur.execute("SELECT category_name, category_id FROM category")
        cat_map = {name: cid for name, cid in cur.fetchall()}

        unknown_cats = {cat for _, cat, *_ in PLANTS if cat not in cat_map}
        if unknown_cats:
            print(f"\nERROR: Unknown category name(s) in PLANTS: {unknown_cats}")
            conn.rollback()
            sys.exit(1)

        plant_rows = [
            (name, cat_map[cat], min6, opt6, min_air, opt_air)
            for name, cat, min6, opt6, min_air, opt_air in PLANTS
        ]

        execute_values(cur, """
            INSERT INTO plants (
                common_name, category_id,
                min_soil_temp_6cm, opt_soil_temp_6cm,
                min_air_temp, opt_air_temp
            ) VALUES %s
        """, plant_rows)
        print(f"  Inserted {len(plant_rows)} plants.")

        # -- Step 5: commit and verify ----------------------------
        conn.commit()
        print("\nAll changes committed.")
        verify(cur)

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\nError — transaction rolled back: {e}")
        sys.exit(1)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
        print("\nDone.")


if __name__ == "__main__":
    main()
