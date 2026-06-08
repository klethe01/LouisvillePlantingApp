"""
Louisville Planting Guide App — ETL Pipeline
============================================================
Extracts hourly temperature data from the Open Meteo API,
transforms it into seed germination risk assessments based
on 6cm soil temperatures, and loads results into PostgreSQL
for use by the Dash application.

Six pipeline stages:
    1. Extract    — pull hourly temps from Open Meteo API
    2. Clean      — parse timestamps to UTC, handle nulls, normalize types
    3. Transform  — run risk engine to score each plant for seed germination
    4. Validate   — data quality checks before DB load
    5. Load       — upsert temps table, refresh risk table
    6. Analytics  — prepare finalized DataFrames for Dash

Usage:
    Standalone:  python LPAmain.py
    From Dash:   from LPAmain import run_etl
                 risk_df, temps_df = run_etl()

Requirements:
    pip install requests psycopg2-binary pandas python-dotenv
    Python 3.9+

Environment (.env file in same directory):
    DB_HOST=
    DB_PORT=5432
    DB_NAME=postgres
    DB_USER=postgres
    DB_PASSWORD=
    DB_SSL=require
    LOG_FILE=LPAmain.log   (optional, defaults to LPAmain.log)
    LOG_LEVEL=INFO          (optional, defaults to INFO)

Developed with and refined using Claude 4.6 Sonnet (Anthropic, 2026),
Google Colaboratory and Gemini 2.5 Flash (2026)
"""

# ============================================================
# IMPORTS
# ============================================================

import logging
import os
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import TypedDict
from zoneinfo import ZoneInfo  # Python 3.9+ standard library

import pandas as pd
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# CONFIGURATION
# ------------------------------------------------------------
# All sensitive values (host, password) come from .env.
# API and timezone constants are fixed for Louisville, KY.
# ============================================================

# Database — values read from .env file
DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     os.getenv("DB_PORT",     "5432"),
    "dbname":   os.getenv("DB_NAME",     "postgres"),
    "user":     os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "sslmode":  os.getenv("DB_SSL",      "require"),
}

# Open Meteo API — Louisville, KY (Zone 7a)
# Returns past 7 days (actual) + next 7 days (forecast) = 336 hourly rows
API_URL = "https://api.open-meteo.com/v1/forecast"
API_PARAMS = {
    "latitude":         38.2542,
    "longitude":        -85.7594,
    "hourly":           "temperature_2m,soil_temperature_6cm",
    "temperature_unit": "fahrenheit",
    "timezone":         "America/New_York",
    "past_days":        7,
    "forecast_days":    7,
}
API_TIMEOUT_SECONDS  = 30
API_MAX_RETRIES      = 3   # retry transient network errors up to 3 times
API_RETRY_DELAY_SECS = 5   # seconds to wait between retry attempts

# Timezones
# API returns timestamps in Eastern time (no timezone suffix on the string).
# All timestamps are converted to UTC before storing in the database.
TZ_EASTERN = ZoneInfo("America/New_York")
TZ_UTC     = timezone.utc

# Plausible temperature bounds for Louisville, KY validation (°F)
# Readings outside these ranges are flagged as suspect in Stage 4.
AIR_TEMP_MIN_PLAUSIBLE  = -30.0
AIR_TEMP_MAX_PLAUSIBLE  = 120.0
SOIL_TEMP_MIN_PLAUSIBLE =   0.0
SOIL_TEMP_MAX_PLAUSIBLE = 120.0

# Logging configuration
LOG_FILE  = os.getenv("LOG_FILE",  "LPAmain.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


# ============================================================
# LOGGING SETUP
# ------------------------------------------------------------
# Two handlers are configured:
#   Console (StreamHandler):       INFO and above — pipeline progress
#   File (RotatingFileHandler):    DEBUG and above — full detail
#
# The file rotates at 2MB and keeps 3 backups so logs don't
# grow unbounded on repeated app restarts.
# ============================================================

def setup_logging() -> logging.Logger:
    """
    Configure and return the application logger named 'LPAmain'.
    Guard against duplicate handlers if called more than once
    (e.g. when Dash hot-reloads the module during development).
    """
    logger = logging.getLogger("LPAmain")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console: INFO and above (or whatever LOG_LEVEL is set to)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    console.setFormatter(fmt)

    # File: DEBUG and above, rotates at 2MB, keeps 3 backups
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


log = setup_logging()


# ============================================================
# DATABASE HELPERS
# ------------------------------------------------------------
# psycopg2 is used as the database connector rather than
# SQLAlchemy. For this pipeline's workload — straightforward
# INSERT/UPDATE/SELECT statements on a small fixed schema —
# psycopg2's direct interface is simpler and avoids the
# overhead of an ORM layer. Both are acceptable connectors
# per the assignment requirements; SQLAlchemy would be the
# better choice if the pipeline needed ORM models, query
# building, or connection pooling across multiple threads.
# ============================================================

def get_connection_string() -> str:
    """Build a psycopg2 DSN string from DB_CONFIG."""
    return (
        f"host={DB_CONFIG['host']} "
        f"port={DB_CONFIG['port']} "
        f"dbname={DB_CONFIG['dbname']} "
        f"user={DB_CONFIG['user']} "
        f"password={DB_CONFIG['password']} "
        f"sslmode={DB_CONFIG['sslmode']}"
    )


@contextmanager
def get_db_connection():
    """
    Context manager that opens a psycopg2 connection and yields it.

    On clean exit:    commits the transaction and closes the connection.
    On any exception: rolls back the transaction, logs a warning,
                      re-raises the exception, and closes the connection.
    """
    conn = None
    try:
        conn = psycopg2.connect(get_connection_string())
        conn.autocommit = False
        log.debug("Database connection opened.")
        yield conn
        conn.commit()
        log.debug("Transaction committed.")
    except Exception:
        if conn:
            conn.rollback()
            log.warning("Transaction rolled back due to error.")
        raise
    finally:
        if conn:
            conn.close()
            log.debug("Database connection closed.")


# ============================================================
# STAGE 1: EXTRACT
# ------------------------------------------------------------
# Calls the Open Meteo API for Louisville, KY and returns the
# raw JSON response dict. Raises RuntimeError on any network
# or HTTP failure so the orchestrator can handle it cleanly.
# ============================================================

def extract_temperatures() -> dict:
    """
    Pull hourly temperature data from the Open Meteo API.

    Variables fetched:
        temperature_2m        — air temp at 2m height (°F)
        soil_temperature_6cm  — soil temp at 6cm depth (°F, for seed germination)

    Returns the raw parsed JSON as a dict.
    Raises RuntimeError on timeout, HTTP error, or unexpected response format.
    """
    log.info("STAGE 1 — Extract: calling Open Meteo API...")
    log.debug("  URL: %s", API_URL)
    log.debug("  Params: %s", API_PARAMS)

    # Retry loop — transient network failures (Timeout, ConnectionError) are
    # retried up to API_MAX_RETRIES times with a short delay between attempts.
    # Non-transient errors (HTTP 4xx/5xx, malformed JSON) raise immediately
    # since retrying won't fix them.
    raw = None
    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            if attempt > 1:
                log.info("  Retry attempt %d of %d...", attempt, API_MAX_RETRIES)
            response = requests.get(
                API_URL, params=API_PARAMS, timeout=API_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            raw = response.json()
            break  # success — exit retry loop

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < API_MAX_RETRIES:
                log.warning(
                    "  Attempt %d/%d failed (%s). Retrying in %ds...",
                    attempt, API_MAX_RETRIES, type(e).__name__, API_RETRY_DELAY_SECS,
                )
                time.sleep(API_RETRY_DELAY_SECS)
            else:
                raise RuntimeError(
                    f"Open Meteo API unreachable after {API_MAX_RETRIES} attempts: {e}"
                )
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"Open Meteo API returned HTTP error: {e}")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Open Meteo API request failed: {e}")
        except ValueError as e:
            raise RuntimeError(f"Open Meteo API returned invalid JSON: {e}")

    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Unexpected API response format: expected JSON object, got {type(raw).__name__}."
        )

    if "hourly" not in raw or not isinstance(raw["hourly"], dict):
        raise RuntimeError(
            "Unexpected API response — 'hourly' key missing or malformed."
        )

    hourly = raw["hourly"]
    required_vars = {"time", "temperature_2m", "soil_temperature_6cm"}
    missing_vars = required_vars - set(hourly.keys())
    if missing_vars:
        raise RuntimeError(
            f"API response missing expected hourly variables: {missing_vars}"
        )

    # Confirm all variable arrays are the same length
    lengths = {var: len(hourly[var]) for var in required_vars}
    if len(set(lengths.values())) != 1:
        raise RuntimeError(
            f"API response hourly variable lengths differ: {lengths}"
        )

    n_rows = len(raw["hourly"]["time"])
    log.info("  Received %d hourly records from API.", n_rows)
    return raw


# ============================================================
# STAGE 2: CLEAN & NORMALIZE
# ------------------------------------------------------------
# Converts raw API JSON into a clean pandas DataFrame:
#   - Parses timestamp strings (Eastern) → UTC-aware datetimes
#   - Adds is_forecast flag (True if timestamp is in the future)
#   - Retains float values in _raw columns for Stage 3 risk math
#   - Rounds float temps → integers for DB storage columns
# ============================================================

def clean_temperatures(raw: dict) -> pd.DataFrame:
    """
    Normalize the raw Open Meteo response into a clean DataFrame.

    The DataFrame contains two sets of temperature columns:
        _raw  (float): used in Stage 3 for accurate threshold comparisons
        int   (Int64): used in Stage 5 for DB storage (nullable integer)

    Returns a DataFrame with columns:
        timestamp       (UTC timezone-aware datetime)
        is_forecast     (bool)
        air_temp        (Int64, rounded °F)
        soil_6cm_temp   (Int64, rounded °F)
        air_temp_raw    (float, kept for Stage 3)
        soil_6cm_raw    (float, kept for Stage 3)
    """
    log.info("STAGE 2 — Clean & Normalize...")

    hourly = raw["hourly"]

    df = pd.DataFrame({
        "timestamp_str": hourly["time"],
        "air_temp_raw":  hourly["temperature_2m"],
        "soil_6cm_raw":  hourly["soil_temperature_6cm"],
    })

    # Log any nulls present in raw data before processing
    null_count = df.isnull().sum().sum()
    if null_count > 0:
        log.warning(
            "  %d null value(s) found in raw API data — "
            "these will be stored as NULL in the temps table.",
            null_count
        )

    # Parse timestamps: API returns naive ISO 8601 strings in Eastern time.
    # Vectorized localize → convert to UTC — faster than row-by-row .apply().
    df["timestamp"] = (
        pd.to_datetime(df["timestamp_str"])
        .dt.tz_localize(TZ_EASTERN)
        .dt.tz_convert("UTC")
    )
    log.debug("  Timestamps parsed and converted from Eastern → UTC.")

    # is_forecast: True if the timestamp is in the future relative to now UTC.
    # Readings at or before the current time are historical actuals.
    now_utc = datetime.now(tz=TZ_UTC)
    df["is_forecast"] = df["timestamp"] > now_utc

    # Round floats to integers for DB storage.
    # Using pandas Int64 (nullable integer) so that NaN from the API
    # is stored as NULL in PostgreSQL rather than crashing the insert.
    df["air_temp"]      = df["air_temp_raw"].round().astype("Int64")
    df["soil_6cm_temp"] = df["soil_6cm_raw"].round().astype("Int64")

    # Keep raw float columns for Stage 3 threshold comparisons
    df = df[[
        "timestamp", "is_forecast",
        "air_temp", "soil_6cm_temp",
        "air_temp_raw", "soil_6cm_raw",   # retained for Stage 3
    ]]

    log.info(
        "  Clean complete: %d rows total | %d forecast | %d actual.",
        len(df),
        int(df["is_forecast"].sum()),
        int((~df["is_forecast"]).sum()),
    )
    return df


# ============================================================
# STAGE 3: TRANSFORM — RISK ENGINE
# ------------------------------------------------------------
# Scores each plant in the DB for seed germination using the
# single lowest hourly reading (raw float) across the full
# 14-day window for air temp and soil temp at 6cm.
#
# Risk logic:
#   HIGH:   min_soil_6cm < plant.min_soil_temp_6cm
#           OR min_air < plant.min_air_temp
#   LOW:    min_soil_6cm >= plant.opt_soil_temp_6cm
#           AND min_air >= plant.opt_air_temp
#   MEDIUM: all other cases (above minimum but not at optimal)
# ============================================================

class RiskRow(TypedDict):
    plant_id:          int
    risk_level:        str
    risk_desc:         str
    min_14day_air:     int
    min_14day_soil6cm: int
    window_start:      datetime
    window_end:        datetime


def _score_risk(
    min_soil:            float,
    min_air:             float,
    min_soil_threshold:  float,
    opt_soil_threshold:  float,
    min_air_threshold:   float,
    opt_air_threshold:   float,
) -> tuple[str, str]:
    """
    Core risk scoring logic for a single plant.
    Compares the 14-day minimum soil (6cm) and air temps against
    the plant's thresholds and returns (risk_level, risk_desc).
    """
    # HIGH RISK — at least one minimum threshold was breached
    if min_soil < min_soil_threshold:
        return (
            "high",
            (
                f"Soil temperature ({min_soil:.1f}°F) dropped below the minimum "
                f"required for seed germination ({min_soil_threshold:.1f}°F) "
                f"within the 14-day window. Not recommended."
            ),
        )
    if min_air < min_air_threshold:
        return (
            "high",
            (
                f"Air temperature ({min_air:.1f}°F) dropped below the minimum "
                f"safe threshold ({min_air_threshold:.1f}°F) within the 14-day "
                f"window. Not recommended."
            ),
        )

    # LOW RISK — both soil and air at or above optimal for the full window
    if min_soil >= opt_soil_threshold and min_air >= opt_air_threshold:
        return (
            "low",
            (
                f"Soil ({min_soil:.1f}°F) and air ({min_air:.1f}°F) temperatures "
                f"remained at or above optimal levels throughout the 14-day window. "
                f"Recommended."
            ),
        )

    # MEDIUM RISK — above minimum thresholds but not yet at optimal
    below_opt = []
    if min_soil < opt_soil_threshold:
        below_opt.append(
            f"soil temp ({min_soil:.1f}°F, optimal {opt_soil_threshold:.1f}°F)"
        )
    if min_air < opt_air_threshold:
        below_opt.append(
            f"air temp ({min_air:.1f}°F, optimal {opt_air_threshold:.1f}°F)"
        )

    return (
        "medium",
        (
            f"Conditions are above minimums but not yet optimal: "
            f"{' and '.join(below_opt)}. May advise waiting."
        ),
    )


def compute_risk(cur, temps_clean: pd.DataFrame) -> list[RiskRow]:
    """
    Run the risk engine against every plant in the DB using the
    current 14-day temperature window from the cleaned DataFrame.

    Uses raw float values (not the rounded integers) for threshold
    comparisons to avoid rounding artifacts at boundary conditions.

    Returns a list of RiskRow dicts ready for insertion into the
    risk table — one row per plant.
    """
    log.info("STAGE 3 — Transform: running risk engine...")

    if temps_clean.empty:
        raise RuntimeError(
            "Cannot compute risk: temperature DataFrame is empty."
        )

    # Compute global minimums across all hourly readings.
    # pandas .min() skips NaN by default so sparse nulls won't
    # corrupt the minimum calculation.
    def _min_or_fail(series: pd.Series, label: str) -> float:
        value = series.dropna().min()
        if pd.isna(value):
            raise RuntimeError(
                f"Cannot compute risk: no valid {label} readings were available."
            )
        return float(value)

    min_air      = _min_or_fail(temps_clean["air_temp_raw"],  "air temperature")
    min_soil_6cm = _min_or_fail(temps_clean["soil_6cm_raw"],  "6cm soil temperature")
    window_start = temps_clean["timestamp"].min()
    window_end   = temps_clean["timestamp"].max()

    log.debug(
        "  14-day window: %s → %s",
        window_start.strftime("%Y-%m-%d %H:%M UTC"),
        window_end.strftime("%Y-%m-%d %H:%M UTC"),
    )
    log.debug(
        "  Global minimums — air: %.1f°F | soil 6cm: %.1f°F",
        min_air, min_soil_6cm,
    )

    # Fetch all plants and their temperature thresholds from the DB
    cur.execute("""
        SELECT
            plant_id, common_name,
            min_soil_temp_6cm, opt_soil_temp_6cm,
            min_air_temp,      opt_air_temp
        FROM plants
        ORDER BY plant_id
    """)
    plants = cur.fetchall()
    log.debug("  Loaded %d plants from DB.", len(plants))

    risk_rows = []

    for (
        plant_id, common_name,
        min_6cm, opt_6cm,
        min_air_thresh, opt_air_thresh,
    ) in plants:

        risk_level, risk_desc = _score_risk(
            min_soil           = min_soil_6cm,
            min_air            = min_air,
            min_soil_threshold = min_6cm,
            opt_soil_threshold = opt_6cm,
            min_air_threshold  = min_air_thresh,
            opt_air_threshold  = opt_air_thresh,
        )
        risk_rows.append({
            "plant_id":          plant_id,
            "risk_level":        risk_level,
            "risk_desc":         risk_desc,
            "min_14day_air":     round(min_air),
            "min_14day_soil6cm": round(min_soil_6cm),
            "window_start":      window_start,
            "window_end":        window_end,
        })

    # Summary breakdown at DEBUG level
    log.debug(
        "  Risk breakdown: %s",
        dict(Counter(r["risk_level"] for r in risk_rows)),
    )
    log.info(
        "  Risk engine complete: %d assessments generated.",
        len(risk_rows),
    )
    return risk_rows


# ============================================================
# STAGE 4: VALIDATE
# ------------------------------------------------------------
# Runs data quality checks on the cleaned temps DataFrame and
# the computed risk rows before writing anything to the DB.
#
# Critical failures (return False) abort the pipeline so no
# bad data is loaded. Warnings are logged but do not abort.
# ============================================================

def validate_temps(temps_clean: pd.DataFrame) -> bool:
    """
    Validate the cleaned temperature DataFrame.

    Critical checks (pipeline aborts on failure):
        - DataFrame is not empty
        - No null timestamps
        - No duplicate timestamps
        - Row count is at least 90% of expected 336 rows

    Warning checks (logged but do not abort):
        - Null values in temperature columns
        - Readings outside plausible bounds for Louisville, KY
        - Missing past or forecast data

    Returns True if all critical checks pass, False otherwise.
    """
    log.info("STAGE 4 — Validate: checking temperature data quality...")
    passed = True

    actual = len(temps_clean)
    if actual == 0:
        log.warning("  [FAIL] No temperature rows present.")
        return False

    timestamps = temps_clean["timestamp"].sort_values()

    if timestamps.isnull().any():
        log.warning("  [FAIL] Null timestamps found in temperature data.")
        return False

    unique_count = timestamps.nunique()
    if unique_count != actual:
        log.warning(
            "  [FAIL] Duplicate timestamps found: %d rows but only %d unique timestamps.",
            actual, unique_count,
        )
        passed = False

    expected    = 14 * 24   # 336 hourly rows
    window_hours = int(
        (timestamps.iloc[-1] - timestamps.iloc[0]) / pd.Timedelta(hours=1)
    ) + 1

    if actual < expected * 0.9:
        log.warning(
            "  [FAIL] Expected ~%d rows, received %d. "
            "API may have returned incomplete data.",
            expected, actual,
        )
        passed = False
    elif actual < window_hours * 0.9:
        log.warning(
            "  [FAIL] Coverage incomplete: expected %d hourly timestamps, received %d.",
            window_hours, actual,
        )
        passed = False
    else:
        log.debug("  [PASS] Row count: %d (expected ~%d).", actual, expected)

    # Warning check: null values in temperature columns
    for col in ["air_temp", "soil_6cm_temp"]:
        n_null = int(temps_clean[col].isnull().sum())
        if n_null > 0:
            log.warning(
                "  [WARN] %d null value(s) in column '%s'. "
                "These will be stored as NULL in the temps table.",
                n_null, col,
            )

    # Warning check: temperature values within plausible bounds
    bounds = [
        ("air_temp_raw",  AIR_TEMP_MIN_PLAUSIBLE,  AIR_TEMP_MAX_PLAUSIBLE,  "air temp"),
        ("soil_6cm_raw",  SOIL_TEMP_MIN_PLAUSIBLE, SOIL_TEMP_MAX_PLAUSIBLE, "soil 6cm"),
    ]
    for col, lo, hi, label in bounds:
        series = temps_clean[col].dropna()
        out_of_range = series[(series < lo) | (series > hi)]
        if len(out_of_range) > 0:
            log.warning(
                "  [WARN] %d %s reading(s) outside plausible range "
                "[%.1f°F, %.1f°F] for Louisville, KY.",
                len(out_of_range), label, lo, hi,
            )

    # Warning check: both past and forecast data present
    if not temps_clean["is_forecast"].any():
        log.warning("  [WARN] No forecast rows found — all data is historical.")
    if temps_clean["is_forecast"].all():
        log.warning("  [WARN] No actual rows found — all data is forecast.")

    if passed:
        log.info("  All critical validation checks passed.")
    return passed


def validate_risk(risk_rows: list[RiskRow], cur=None) -> bool:
    """
    Validate computed risk rows before loading.

    Critical checks (pipeline aborts on failure):
        - At least one row was generated
        - All required fields are present on every row
        - risk_level values are one of: low, medium, high
        - All plant_ids exist in the plants table (referential integrity)

    Returns True if all critical checks pass, False otherwise.
    """
    log.info("  Validating %d risk rows...", len(risk_rows))
    passed = True

    if not risk_rows:
        log.warning("  [FAIL] No risk rows generated. Risk table would be empty.")
        return False

    required_fields = {
        "plant_id", "risk_level", "risk_desc",
        "min_14day_air", "min_14day_soil6cm",
        "window_start", "window_end",
    }
    valid_risk_levels = {"low", "medium", "high"}

    for i, row in enumerate(risk_rows):
        missing = required_fields - set(row.keys())
        if missing:
            log.warning("  [FAIL] Row %d missing fields: %s", i, missing)
            passed = False

        if row.get("risk_level") not in valid_risk_levels:
            log.warning(
                "  [FAIL] Row %d invalid risk_level: '%s'",
                i, row.get("risk_level"),
            )
            passed = False

    # Referential integrity check — confirm every plant_id in the risk rows
    # exists in the plants table before attempting the DB insert.
    # While the FK constraint would catch this at insert time, catching it
    # here produces a clearer error message and avoids a partial transaction.
    if cur is not None:
        plant_ids_in_rows = {r["plant_id"] for r in risk_rows}
        cur.execute(
            "SELECT plant_id FROM plants WHERE plant_id = ANY(%s)",
            (list(plant_ids_in_rows),)
        )
        found_ids   = {row[0] for row in cur.fetchall()}
        missing_ids = plant_ids_in_rows - found_ids
        if missing_ids:
            log.warning(
                "  [FAIL] Risk rows reference plant_id(s) not found in plants table: %s",
                sorted(missing_ids),
            )
            passed = False
        else:
            log.debug(
                "  [PASS] Referential integrity confirmed: all %d plant_id(s) verified.",
                len(plant_ids_in_rows),
            )

    # Log risk level distribution as an informational summary
    level_counts = Counter(r["risk_level"] for r in risk_rows)
    log.info(
        "  Risk distribution — low: %d | medium: %d | high: %d",
        level_counts.get("low",    0),
        level_counts.get("medium", 0),
        level_counts.get("high",   0),
    )

    if passed:
        log.debug("  All risk validation checks passed.")
    return passed


# ============================================================
# STAGE 5: LOAD
# ------------------------------------------------------------
# Two load operations run inside the same DB transaction:
#
#   load_temps: upserts all 336 hourly rows into temps.
#       ON CONFLICT DO UPDATE overwrites existing rows with
#       fresh API data, keeping forecasts current.
#       Stale rows outside the 14-day window are deleted.
#
#   load_risk: uses full refresh (delete-then-insert) rather than
#       incremental loading. Risk assessments are derived metrics
#       recomputed in full on every run — there is no meaningful
#       "new record" concept. Every execution produces a complete
#       fresh set of scores for all plants based on the current
#       14-day window, so appending or updating individual rows
#       would leave stale assessments from prior runs in place.
#       Full refresh is therefore the correct strategy here.
# ============================================================

def load_temps(cur, temps_for_db: pd.DataFrame, window_start: datetime, window_end: datetime) -> int:
    """
    Upsert temperature rows into the temps table.

    ON CONFLICT (timestamp) DO UPDATE overwrites existing rows so
    that forecast accuracy improves as actual readings come in.
    After upserting, deletes any rows that fall outside the current
    14-day window to keep the table bounded in size.

    Expects a DataFrame with columns:
        timestamp, is_forecast, air_temp, soil_6cm_temp

    Returns the number of rows upserted.
    """
    log.info("STAGE 5 — Load: upserting %d rows into temps...", len(temps_for_db))

    # Build list of tuples using zip() across individual columns.
    # Null-safe: pd.isna() handles both float NaN and pd.NA (Int64),
    # converting missing values to Python None so psycopg2 stores NULL.
    rows = list(zip(
        temps_for_db["timestamp"],
        temps_for_db["is_forecast"].astype(bool),
        [None if pd.isna(v) else int(v) for v in temps_for_db["air_temp"]],
        [None if pd.isna(v) else int(v) for v in temps_for_db["soil_6cm_temp"]],
    ))

    psycopg2.extras.execute_values(cur, """
        INSERT INTO temps (timestamp, is_forecast, air_temp, soil_6cm_temp)
        VALUES %s
        ON CONFLICT (timestamp) DO UPDATE SET
            is_forecast   = EXCLUDED.is_forecast,
            air_temp      = EXCLUDED.air_temp,
            soil_6cm_temp = EXCLUDED.soil_6cm_temp,
            fetched_at    = NOW()
    """, rows)

    # Remove any rows now outside the current 14-day window
    cur.execute("""
        DELETE FROM temps
        WHERE timestamp < %s OR timestamp > %s
    """, (window_start, window_end))
    deleted = cur.rowcount
    if deleted > 0:
        log.debug("  Removed %d stale temps row(s) outside the 14-day window.", deleted)

    log.info("  temps load complete: %d rows upserted.", len(temps_for_db))
    return len(rows)


def load_risk(cur, risk_rows: list[RiskRow]) -> int:
    """
    Replace all risk rows with freshly computed assessments.
    Delete-then-insert ensures no stale rows remain if the
    plant list changes between pipeline runs.
    Returns the number of rows inserted.
    """
    log.info("  Loading %d risk rows...", len(risk_rows))

    cur.execute("DELETE FROM risk")
    deleted = cur.rowcount
    log.debug("  Cleared %d existing risk row(s).", deleted)

    psycopg2.extras.execute_values(cur, """
        INSERT INTO risk (
            plant_id, risk_level, risk_desc,
            min_14day_air, min_14day_soil6cm,
            window_start, window_end
        ) VALUES %s
    """, [
        (
            r["plant_id"],
            r["risk_level"],
            r["risk_desc"],
            r["min_14day_air"],
            r["min_14day_soil6cm"],
            r["window_start"],
            r["window_end"],
        )
        for r in risk_rows
    ])

    log.info("  risk load complete: %d rows inserted.", len(risk_rows))
    return len(risk_rows)


# ============================================================
# STAGE 6: ANALYTICS PREP
# ------------------------------------------------------------
# Queries the DB and builds two finalized DataFrames for the
# Dash application. No dashboard code is included here —
# these DataFrames are the hand-off point between the ETL
# pipeline and the Dash layout/callbacks.
#
#   risk_df:
#       One row per plant with risk level and metadata.
#       Joined with plants and category so the Dash app has
#       everything it needs for the recommendation list and
#       category filters without additional queries.
#
#   temps_out_df:
#       One row per hourly timestamp.
#       Used directly for the air temp and soil 6cm
#       time-series charts in the temperature dashboard.
# ============================================================

def prepare_analytics(cur) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Query finalized datasets from the DB for the Dash application.

    Returns:
        risk_df      (pd.DataFrame): risk assessments joined with plant and
                                     category data, ordered by risk level then
                                     category then plant name.
        temps_out_df (pd.DataFrame): hourly temperature readings ordered
                                     chronologically.
    """
    log.info("STAGE 6 — Analytics Prep: querying finalized datasets for Dash...")

    # Risk dataset — joins risk + plants + category into one flat table.
    # Includes plant threshold columns so Dash can show context
    # (e.g. "your soil temp is 62°F, this plant needs 65°F").
    cur.execute("""
        SELECT
            r.risk_id,
            p.common_name,
            c.category_name,
            r.risk_level,
            r.risk_desc,
            r.min_14day_air,
            r.min_14day_soil6cm,
            r.window_start,
            r.window_end,
            r.risk_time,
            p.min_soil_temp_6cm,
            p.opt_soil_temp_6cm,
            p.min_air_temp,
            p.opt_air_temp
        FROM risk r
        JOIN plants   p ON p.plant_id    = r.plant_id
        JOIN category c ON c.category_id = p.category_id
        ORDER BY
            CASE r.risk_level
                WHEN 'low'    THEN 1
                WHEN 'medium' THEN 2
                WHEN 'high'   THEN 3
            END,
            c.category_name,
            p.common_name
    """)
    risk_cols = [desc[0] for desc in cur.description]
    risk_df   = pd.DataFrame(cur.fetchall(), columns=risk_cols)

    # Temperature dataset — air and soil 6cm hourly readings for charts
    cur.execute("""
        SELECT timestamp, is_forecast, air_temp, soil_6cm_temp
        FROM temps
        ORDER BY timestamp
    """)
    temps_cols   = [desc[0] for desc in cur.description]
    temps_out_df = pd.DataFrame(cur.fetchall(), columns=temps_cols)

    log.info(
        "  Analytics ready — risk_df: %d rows | temps_df: %d rows.",
        len(risk_df), len(temps_out_df),
    )
    return risk_df, temps_out_df


# ============================================================
# ORCHESTRATOR
# ------------------------------------------------------------
# run_etl() is the single entry point for the pipeline.
# Called by the Dash app on startup and by this script when
# run directly from the command line.
#
# All six stages run in sequence. Stages 3–6 share a single
# DB transaction — if any stage fails, the whole transaction
# rolls back so the database is never left in a partial state.
#
# Returns (risk_df, temps_df) on success for use by Dash.
# ============================================================

def run_etl() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Execute the complete ETL pipeline from extraction to analytics prep.

    Called from the Dash app on startup:
        from LPAmain import run_etl
        risk_df, temps_df = run_etl()

    Returns:
        risk_df   (pd.DataFrame): seed germination risk assessments with
                                  plant/category metadata — for the
                                  recommendation list.
        temps_df  (pd.DataFrame): hourly air and 6cm soil temperature
                                  readings — for the charts.
    """
    log.info("=" * 60)
    log.info("Louisville Planting Guide App — ETL pipeline starting.")
    log.info("=" * 60)

    pipeline_start = datetime.now(tz=TZ_UTC)

    try:
        # Stage 1: Extract raw data from Open Meteo API
        raw = extract_temperatures()

        # Stage 2: Clean and normalize the raw API response
        temps_clean = clean_temperatures(raw)

        # Stage 4a: Validate temps before opening a DB connection.
        # A bad API response shouldn't start a transaction unnecessarily.
        if not validate_temps(temps_clean):
            raise RuntimeError(
                "Temperature data failed critical validation. "
                "Pipeline aborted to prevent loading bad data."
            )

        # Stages 3–6 share a single DB transaction
        with get_db_connection() as conn:
            cur = conn.cursor()

            # Stage 3: Transform — compute risk assessments from the
            # in-memory temps DataFrame and the plants table in the DB
            risk_rows = compute_risk(cur, temps_clean)

            # Stage 4b: Validate risk rows before loading
            if not validate_risk(risk_rows, cur):
                raise RuntimeError(
                    "Risk rows failed validation. Pipeline aborted."
                )

            # Drop raw float columns — used only in Stages 3 and 4a.
            # Pass only the four DB columns to load_temps().
            temps_for_db = temps_clean[[
                "timestamp", "is_forecast",
                "air_temp", "soil_6cm_temp",
            ]]

            # Compute window bounds for the stale-row cleanup in load_temps
            window_start = temps_clean["timestamp"].min()
            window_end   = temps_clean["timestamp"].max()

            # Stage 5: Load temps (upsert) and risk (replace) into DB
            load_temps(cur, temps_for_db, window_start, window_end)
            load_risk(cur, risk_rows)

            # Stage 6: Query finalized datasets for Dash
            risk_df, temps_df = prepare_analytics(cur)
            # Transaction commits here when the with block exits cleanly

    except RuntimeError:
        raise
    except psycopg2.OperationalError as e:
        log.error("Database connection failed: %s", e)
        raise RuntimeError(f"Database connection failed: {e}") from e
    except Exception as e:
        log.error("Unexpected error in ETL pipeline: %s", e, exc_info=True)
        raise

    elapsed = (datetime.now(tz=TZ_UTC) - pipeline_start).total_seconds()
    log.info("ETL pipeline completed successfully in %.2fs.", elapsed)
    log.info("=" * 60)

    return risk_df, temps_df


# ============================================================
# ENTRY POINT
# ------------------------------------------------------------
# Allows running the pipeline directly from the command line:
#   python LPAmain.py
#
# When integrated into the Dash app, run_etl() is imported
# and called directly — the __main__ block is not used.
# ============================================================

if __name__ == "__main__":
    try:
        risk_df, temps_df = run_etl()
        print(f"\nPipeline complete.")
        print(f"  Risk assessments : {len(risk_df):>4} rows")
        print(f"  Temperature reads: {len(temps_df):>4} rows")
        print(f"\nLog written to: {LOG_FILE}")
    except RuntimeError as e:
        print(f"\nPipeline failed: {e}")
        sys.exit(1)
