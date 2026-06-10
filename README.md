# Louisville Planting Guide App

Interactive seed germination recommendation dashboard for Louisville, KY (Zone 7a). Connects to a live PostgreSQL database populated by a 14-day weather ETL pipeline and provides planting risk assessments for 47 plants.

---

## Business Insights

The app answers one practical question for Louisville gardeners: **"What can I safely plant direct-to-seed in the ground this week?"**

It does this by pulling the past 7 days of actual soil and air temperatures and the next 7-day forecast from Open Meteo, comparing the lowest soil temperature reading in that 14-day window against each plant's known germination minimums, checking that air temperature stayed at or above the universal safe threshold of 40°F, and assigning one of three risk levels:

- **Recommended (low)** — all soil temperature readings have remained at or above the plant's optimal germination threshold and air temperature has stayed at or above 40°F throughout the 14-day window. Safe to plant now.
- **May Advise Waiting (medium)** — all soil temperature readings are above the plant's minimum requirement and air temperature has stayed at or above 40°F, but soil temperatures have not yet reached the optimal level. Germination is possible but not ideal.
- **Not Recommended (high)** — at least one soil temperature reading fell below the plant's minimum germination requirement, or air temperature dropped below 40°F at any point in the 14-day window. Risk of failed germination or frost damage.

---

## How to Run

### Prerequisites

1. Python 3.9 or higher
2. A Supabase (or any PostgreSQL) database initialized with `LPAloadscript.py`
3. A `.env` file in the project root (see below)

### Install dependencies

```bash
pip install -r requirements.txt
```

### Create your `.env` file

Create a file named `.env` in the same directory as the scripts. **Do not commit this file — it is listed in `.gitignore`.**

```
DB_HOST=aws-0-us-east-1.pooler.supabase.com
DB_PORT=6543
DB_NAME=postgres
DB_USER=postgres.your-project-ref
DB_PASSWORD=your_password_here
DB_SSL=require
```

### Step 1 — Initialize the database (one time only)

```bash
python LPAloadscript.py
```

This creates the four database tables (`category`, `plants`, `temps`, `risk`) and seeds 47 plants with their germination temperature thresholds. Only needs to run once, or again if you want to reset the database.

### Step 2 — Run the dashboard

```bash
python LPAdash.py
```

Open your browser to **http://localhost:8050**

The app runs the full ETL pipeline on startup — calling the Open Meteo API, computing risk assessments, and loading results into the database before serving the dashboard. This typically takes 5–15 seconds on first load.

---

## Dashboard Features

### KPI Cards
Four summary metrics at the top of the page:
- **Recommended to Plant** — count of low-risk plants
- **May Advise Waiting** — count of medium-risk plants
- **Not Recommended** — count of high-risk plants
- **14-Day Min Soil Temp** — the lowest 6cm soil temperature recorded or forecast in the current window

### 14-Day Temperature Dashboard
Time series chart showing:
- Air temperature (blue) and soil temperature at 6cm (green)
- Solid lines for actual past readings, dashed lines for forecast
- Vertical marker at the current time separating past from forecast
- Reference line at 40°F marking the minimum safe air temperature

### Soil Temperature Gap Chart
Horizontal bar chart showing how far the current minimum soil temperature is above or below each plant's germination requirement. A positive gap means soil is warm enough; negative means too cold. Plants are sorted from easiest to hardest to grow in current conditions and color-coded by risk level.

### Plant Recommendations Table
Sortable and filterable table of all 47 plants with their risk level, minimum soil temperature requirement, and plain-English assessment. Rows are color-coded green / amber / red by risk level.

### Filters
- **Category** — filter by Vegetable/Fruit, Herb, or Flower
- **Risk level** — show only Recommended, May Advise Waiting, or Not Recommended plants
Both filters update the chart and table simultaneously without restarting the app.

---

## Project Structure

```
LouisvillePlantingApp/
├── .env                   # Credentials — never commit (in .gitignore)
├── .gitignore
├── LPAloadscript.py       # One-time DB initialization and plant seeding
├── LPAmain.py             # ETL pipeline (6 stages: extract → analytics)
├── LPAdash.py             # Dash MVP dashboard
├── requirements.txt
└── README.md
```

---

## Dependencies

```
dash
dash-bootstrap-components
plotly
pandas
psycopg2-binary
python-dotenv
requests
```

Install all at once:

```bash
pip install -r requirements.txt
```

---

## Data Sources

- **Open Meteo API** — hourly air temperature and soil temperature at 6cm depth, past 7 days actual + 7-day forecast, Louisville KY (38.2542°N, 85.7594°W)
- **Old Farmer's Almanac** — seed germination temperature ranges ([almanac.com](https://www.almanac.com/soil-temperature-chart))
- **Mississippi Foundation for Renewable Energy** — seed germination temperature chart ([backwoodsenergy.org](https://www.backwoodsenergy.org/seed-germination-temperature-chart.html))

---

*Developed by Katie Etheridge Davis · Louisville, KY · Zone 7a*
