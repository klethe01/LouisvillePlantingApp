# ============================================================
# Louisville Planting Guide App
# Initial PostgreSQL Load Script — Python version
# Requires: psycopg2-binary, python-dotenv
# Install:  pip install psycopg2-binary python-dotenv
# Script generated and refined using Claude 4.6 Sonnet (Anthropic, 2026)
# ============================================================

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
import os
import sys

load_dotenv()

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     os.getenv("DB_PORT",     "5432"),
    "dbname":   os.getenv("DB_NAME",     "louisville_planting"),
    "user":     os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}


# ============================================================
# DDL — drop and recreate all tables
# ============================================================

DROP_TABLES = """
DROP TABLE IF EXISTS risk    CASCADE;
DROP TABLE IF EXISTS temps   CASCADE;
DROP TABLE IF EXISTS plants  CASCADE;
DROP TABLE IF EXISTS category CASCADE;
"""

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

CREATE_PLANTS = """
CREATE TABLE plants (
    plant_id            SERIAL   PRIMARY KEY,
    common_name         TEXT     NOT NULL,
    category_id         INTEGER  NOT NULL
                                 REFERENCES category (category_id)
                                 ON DELETE RESTRICT,
    min_soil_temp_6cm   FLOAT    NOT NULL CHECK (min_soil_temp_6cm >= 0),
    opt_soil_temp_6cm   FLOAT    NOT NULL CHECK (opt_soil_temp_6cm >= min_soil_temp_6cm),
    min_soil_temp_18cm  FLOAT             CHECK (min_soil_temp_18cm >= 0),
    opt_soil_temp_18cm  FLOAT             CHECK (
                                              opt_soil_temp_18cm IS NULL
                                              OR opt_soil_temp_18cm >= min_soil_temp_18cm
                                          ),
    min_air_temp        FLOAT    NOT NULL,
    opt_air_temp        FLOAT    NOT NULL CHECK (opt_air_temp >= min_air_temp),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  plants                       IS 'Static plant reference data. One row per plant.';
COMMENT ON COLUMN plants.plant_id              IS 'Surrogate primary key.';
COMMENT ON COLUMN plants.common_name           IS 'Human-readable plant name (e.g. tomato, basil).';
COMMENT ON COLUMN plants.category_id           IS 'FK to category table.';
COMMENT ON COLUMN plants.min_soil_temp_6cm     IS 'Minimum soil temp in F at 6cm for seed germination.';
COMMENT ON COLUMN plants.opt_soil_temp_6cm     IS 'Optimal soil temp in F at 6cm for seed germination.';
COMMENT ON COLUMN plants.min_soil_temp_18cm    IS 'Minimum soil temp in F at 18cm for transplants. NULL if unavailable.';
COMMENT ON COLUMN plants.opt_soil_temp_18cm    IS 'Optimal soil temp in F at 18cm for transplants. NULL if unavailable.';
COMMENT ON COLUMN plants.min_air_temp          IS 'Minimum air temp in F required for safe planting.';
COMMENT ON COLUMN plants.opt_air_temp          IS 'Optimal air temp in F for best results.';
CREATE INDEX idx_plants_category_id ON plants (category_id);
"""

CREATE_TEMPS = """
CREATE TABLE temps (
    timestamp       TIMESTAMPTZ  PRIMARY KEY,
    is_forecast     BOOLEAN      NOT NULL,
    air_temp        INTEGER      NOT NULL,
    soil_6cm_temp   INTEGER      NOT NULL,
    soil_18cm_temp  INTEGER      NOT NULL,
    fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  temps                  IS 'Hourly air and soil temperature readings from Open Meteo API.';
COMMENT ON COLUMN temps.timestamp        IS 'Datetime of the reading (America/New_York, stored as UTC). Natural primary key.';
COMMENT ON COLUMN temps.is_forecast      IS 'TRUE = predicted future reading; FALSE = actual past reading.';
COMMENT ON COLUMN temps.air_temp         IS 'Air temperature in Fahrenheit at 2m height.';
COMMENT ON COLUMN temps.soil_6cm_temp    IS 'Soil temperature in Fahrenheit at 6cm depth (seeds).';
COMMENT ON COLUMN temps.soil_18cm_temp   IS 'Soil temperature in Fahrenheit at 18cm depth (transplants).';
COMMENT ON COLUMN temps.fetched_at       IS 'When this row was inserted from the API.';
CREATE INDEX idx_temps_timestamp ON temps (timestamp DESC);
"""

CREATE_RISK = """
CREATE TABLE risk (
    risk_id             SERIAL       PRIMARY KEY,
    plant_id            INTEGER      NOT NULL
                                     REFERENCES plants (plant_id)
                                     ON DELETE CASCADE,
    risk_time           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    planting_type       TEXT         NOT NULL CHECK (planting_type IN ('seed', 'transplant')),
    risk_level          TEXT         NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    risk_desc           TEXT,
    min_14day_air       INTEGER      NOT NULL,
    min_14day_soil6cm   INTEGER      NOT NULL,
    min_14day_soil18cm  INTEGER      NOT NULL,
    window_start        TIMESTAMPTZ  NOT NULL,
    window_end          TIMESTAMPTZ  NOT NULL
);
COMMENT ON TABLE  risk                     IS 'Risk assessment computed per plant per app run.';
COMMENT ON COLUMN risk.risk_id             IS 'Surrogate primary key.';
COMMENT ON COLUMN risk.plant_id            IS 'FK to plants table.';
COMMENT ON COLUMN risk.risk_time           IS 'Timestamp when this assessment was computed.';
COMMENT ON COLUMN risk.planting_type       IS 'Whether assessment is for seed or transplant planting.';
COMMENT ON COLUMN risk.risk_level          IS 'Resulting risk tier: low, medium, or high.';
COMMENT ON COLUMN risk.risk_desc           IS 'Human-readable explanation of the risk level assigned.';
COMMENT ON COLUMN risk.min_14day_air       IS 'Lowest air temp in F across the 14-day window.';
COMMENT ON COLUMN risk.min_14day_soil6cm   IS 'Lowest soil temp in F at 6cm across the 14-day window.';
COMMENT ON COLUMN risk.min_14day_soil18cm  IS 'Lowest soil temp in F at 18cm across the 14-day window.';
COMMENT ON COLUMN risk.window_start        IS 'Start of the 14-day temperature window used in this assessment.';
COMMENT ON COLUMN risk.window_end          IS 'End of the 14-day temperature window used in this assessment.';
CREATE INDEX idx_risk_plant_id  ON risk (plant_id);
CREATE INDEX idx_risk_risk_time ON risk (risk_time DESC);
"""


# ============================================================
# Seed data
# ============================================================

CATEGORIES = [
    ("vegetable/fruit", "Edible crops including vegetables, fruiting plants, and berries grown for harvest."),
    ("herb",            "Aromatic or culinary plants grown for their leaves, seeds, or flavor."),
    ("flower",          "Ornamental flowering plants grown for aesthetics or pollinator support."),
]

# (common_name, category_name,
#  min_soil_6cm, opt_soil_6cm,
#  min_soil_18cm, opt_soil_18cm,
#  min_air, opt_air)
PLANTS = [
    # Vegetables / fruit
    ("asparagus",       "vegetable/fruit",  50, 77,   50,   75,   40, 75),
    ("bean",            "vegetable/fruit",  60, 85,   60,   80,   50, 80),
    ("beet",            "vegetable/fruit",  40, 85,   40,   75,   40, 75),
    ("blackberry",      "vegetable/fruit",  45, 75,   45,   70,   40, 75),
    ("cabbage",         "vegetable/fruit",  45, 85,   40,   75,   40, 75),
    ("carrot",          "vegetable/fruit",  45, 85,   45,   75,   40, 75),
    ("celery",          "vegetable/fruit",  60, 70,   60,   70,   50, 75),
    ("chard",           "vegetable/fruit",  50, 85,   50,   75,   40, 75),
    ("collard",         "vegetable/fruit",  45, 85,   45,   75,   40, 75),
    ("cucumber",        "vegetable/fruit",  60, 95,   65,   85,   60, 85),
    ("eggplant",        "vegetable/fruit",  60, 95,   65,   85,   60, 85),
    ("gourds",          "vegetable/fruit",  70, 95,   70,   90,   60, 85),
    ("ground cherry",   "vegetable/fruit",  65, 85,   65,   80,   55, 80),
    ("leek",            "vegetable/fruit",  50, 77,   50,   70,   40, 75),
    ("lettuce",         "vegetable/fruit",  35, 75,   35,   65,   40, 70),
    ("melon",           "vegetable/fruit",  70, 95,   70,   90,   60, 85),
    ("okra",            "vegetable/fruit",  65, 95,   65,   90,   60, 90),
    ("onion",           "vegetable/fruit",  35, 85,   35,   75,   40, 75),
    ("parsnip",         "vegetable/fruit",  35, 70,   35,   65,   40, 70),
    ("sweet pea",       "vegetable/fruit",  40, 75,   40,   70,   40, 70),
    ("southern pea",    "vegetable/fruit",  60, 95,   60,   85,   55, 85),
    ("pepper",          "vegetable/fruit",  65, 95,   65,   85,   60, 85),
    ("pumpkin",         "vegetable/fruit",  60, 95,   65,   85,   60, 85),
    ("radish",          "vegetable/fruit",  40, 90,   40,   80,   40, 75),
    ("sorghum",         "vegetable/fruit",  60, 95,   60,   90,   60, 90),
    ("spinach",         "vegetable/fruit",  35, 75,   35,   65,   40, 65),
    ("squash",          "vegetable/fruit",  60, 95,   65,   85,   60, 85),
    ("strawberry",      "vegetable/fruit",  50, 80,   50,   75,   40, 75),
    ("sweet corn",      "vegetable/fruit",  50, 95,   55,   85,   55, 85),
    ("tomatillo",       "vegetable/fruit",  65, 85,   65,   80,   55, 80),
    ("tomato",          "vegetable/fruit",  60, 85,   65,   80,   55, 80),
    ("turnip",          "vegetable/fruit",  40, 85,   40,   75,   40, 75),
    # Flowers
    ("cosmos",          "flower",           65, 85,   None, None, 55, 80),
    ("marigold",        "flower",           65, 85,   65,   80,   55, 80),
    ("senna",           "flower",           65, 85,   None, None, 55, 80),
    ("sunflower",       "flower",           55, 85,   55,   80,   50, 80),
    ("zinnia",          "flower",           70, 85,   70,   85,   60, 85),
    # Herbs
    ("basil",           "herb",             65, 85,   65,   80,   60, 80),
    ("chives",          "herb",             50, 85,   50,   75,   40, 75),
    ("cilantro",        "herb",             55, 75,   55,   70,   40, 70),
    ("dill",            "herb",             60, 70,   60,   70,   45, 70),
    ("mint",            "herb",             55, 70,   55,   70,   45, 70),
    ("mustard",         "herb",             40, 75,   40,   70,   40, 70),
    ("oregano",         "herb",             65, 85,   65,   80,   55, 80),
    ("parsley",         "herb",             50, 85,   50,   75,   40, 75),
    ("sage",            "herb",             60, 85,   60,   80,   50, 80),
    ("thyme",           "herb",             60, 85,   60,   80,   50, 80),
]


# ============================================================
# Helpers
# ============================================================

def run_ddl(cur, sql, label):
    print(f"  {label}... ", end="", flush=True)
    cur.execute(sql)
    print("done")


def verify(cur):
    print("\nVerification")
    print("-" * 40)

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
# Main
# ============================================================

def main():
    print("Connecting to database...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        cur = conn.cursor()
    except psycopg2.OperationalError as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    try:
        print("\nDropping existing tables...")
        cur.execute(DROP_TABLES)

        print("\nCreating tables...")
        run_ddl(cur, CREATE_CATEGORY, "category")
        run_ddl(cur, CREATE_PLANTS,   "plants")
        run_ddl(cur, CREATE_TEMPS,    "temps")
        run_ddl(cur, CREATE_RISK,     "risk")

        print("\nSeeding category...")
        execute_values(
            cur,
            "INSERT INTO category (category_name, category_desc) VALUES %s",
            CATEGORIES
        )
        print(f"  Inserted {len(CATEGORIES)} categories.")

        print("\nSeeding plants...")
        cur.execute("SELECT category_name, category_id FROM category")
        cat_map = {name: cid for name, cid in cur.fetchall()}

        plant_rows = [
            (
                name,
                cat_map[cat],
                min6, opt6,
                min18, opt18,
                min_air, opt_air,
            )
            for name, cat, min6, opt6, min18, opt18, min_air, opt_air in PLANTS
        ]

        execute_values(cur, """
            INSERT INTO plants (
                common_name, category_id,
                min_soil_temp_6cm, opt_soil_temp_6cm,
                min_soil_temp_18cm, opt_soil_temp_18cm,
                min_air_temp, opt_air_temp
            ) VALUES %s
        """, plant_rows)
        print(f"  Inserted {len(plant_rows)} plants.")

        conn.commit()
        print("\nAll changes committed.")

        verify(cur)

    except Exception as e:
        conn.rollback()
        print(f"\nError — transaction rolled back: {e}")
        sys.exit(1)

    finally:
        cur.close()
        conn.close()
        print("\nDone.")


if __name__ == "__main__":
    main()
