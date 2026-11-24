"""Stage 7 - exploratory data analysis and visualisation.

Run:  python -m src.analysis

The data is read from the SQLite database (the output of the loader), so the
analysis is connected to the previous pipeline stages. Figures are written to
figures/, numbers and generated findings to data/eda_summary.json.
"""
import sqlite3
import sys

import matplotlib

matplotlib.use("Agg")   # no window needed - write PNG files only
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from src import config, summary

CATEGORY_ORDER = ["Good", "Fair", "Moderate", "Poor", "Very poor", "Extremely poor", "Unknown"]
CATEGORY_COLORS = ["#2a9d8f", "#8ab17d", "#e9c46a", "#f4a261", "#e76f51", "#9d0208", "#b0b0b0"]
CITY_COLORS = {"Almaty": "#1f77b4", "Astana": "#d62728", "Shymkent": "#2ca02c", "Karaganda": "#ff7f0e"}

QUERY = """
SELECT m.record_id, c.city_name AS city, c.region, m.observed_at, m.observed_date, m.hour, m.day_period,
       m.is_weekend, m.pm10, m.pm2_5, m.carbon_monoxide, m.nitrogen_dioxide, m.sulphur_dioxide, m.ozone,
       m.european_aqi, m.aqi_category, m.pm25_exceeds_who, m.pm2_5_log, m.pm_ratio, m.pm2_5_zscore
FROM measurements m JOIN cities c ON c.city_id = m.city_id
ORDER BY c.city_name, m.observed_at
"""


def load_from_database() -> pd.DataFrame:
    connection = sqlite3.connect(config.DB_PATH)
    try:
        frame = pd.read_sql_query(QUERY, connection)
    finally:
        connection.close()
    frame["observed_at"] = pd.to_datetime(frame["observed_at"])
    return frame


def save(fig, name: str) -> str:
    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.FIGURES_DIR / name
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[analysis] figure saved: figures/{name}", flush=True)
    return name


def make_figures(df: pd.DataFrame) -> dict:
    sns.set_theme(style="whitegrid")
    figures = {}
    cities = [c for c in config.CITY_NAMES if c in set(df["city"])]
    palette = {c: CITY_COLORS.get(c, "#555555") for c in cities}

    # 1. mean PM2.5 per city (bar + std error bar) with the WHO guideline ------------------
    stats = df.groupby("city")["pm2_5"].agg(["mean", "std"]).reindex(cities)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(stats.index, stats["mean"], yerr=stats["std"], capsize=5, color=[palette[c] for c in stats.index])
    ax.axhline(config.WHO_PM25_24H, color="black", linestyle="--", label=f"WHO 24-h guideline ({config.WHO_PM25_24H:.0f} µg/m³)")
    ax.set_title("Mean hourly PM2.5 concentration by city")
    ax.set_xlabel("City")
    ax.set_ylabel("PM2.5, µg/m³ (error bar = 1 standard deviation)")
    ax.legend()
    figures["fig1_mean_pm25_by_city.png"] = save(fig, "fig1_mean_pm25_by_city.png")

    # 2. daily mean PM2.5 trend ------------------------------------------------------------
    daily = df.groupby(["observed_date", "city"])["pm2_5"].mean().unstack("city")[cities]
    fig, ax = plt.subplots(figsize=(10, 5))
    for city in cities:
        ax.plot(pd.to_datetime(daily.index), daily[city], marker="o", label=city, color=palette[city])
    ax.axhline(config.WHO_PM25_24H, color="black", linestyle="--", linewidth=1, label="WHO 24-h guideline")
    ax.set_title("Daily mean PM2.5 concentration")
    ax.set_xlabel("Date")
    ax.set_ylabel("PM2.5, µg/m³")
    ax.legend(ncol=3)
    fig.autofmt_xdate()
    figures["fig2_daily_pm25_trend.png"] = save(fig, "fig2_daily_pm25_trend.png")

    # 3. heat map: city x hour of day ------------------------------------------------------
    pivot = df.pivot_table(index="city", columns="hour", values="pm2_5", aggfunc="mean").reindex(cities)
    fig, ax = plt.subplots(figsize=(11, 4))
    sns.heatmap(pivot, cmap="YlOrRd", ax=ax, cbar_kws={"label": "mean PM2.5, µg/m³"})
    ax.set_title("Mean PM2.5 by city and hour of the day")
    ax.set_xlabel("Hour of the day (local time)")
    ax.set_ylabel("City")
    figures["fig3_heatmap_city_hour.png"] = save(fig, "fig3_heatmap_city_hour.png")

    # 4. distribution (box plot) -----------------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.boxplot(data=df, x="city", y="pm2_5", order=cities, hue="city", palette=palette, legend=False, ax=ax)
    ax.axhline(config.WHO_PM25_24H, color="black", linestyle="--", linewidth=1, label="WHO 24-h guideline")
    ax.set_title("Distribution of hourly PM2.5 by city")
    ax.set_xlabel("City")
    ax.set_ylabel("PM2.5, µg/m³")
    ax.legend()
    figures["fig4_pm25_boxplot.png"] = save(fig, "fig4_pm25_boxplot.png")

    # 5. share of AQI categories -----------------------------------------------------------
    shares = pd.crosstab(df["city"], df["aqi_category"], normalize="index").reindex(cities)
    present = [c for c in CATEGORY_ORDER if c in shares.columns]
    shares = shares[present] * 100
    fig, ax = plt.subplots(figsize=(9, 5))
    shares.plot(kind="bar", stacked=True, ax=ax, color=[CATEGORY_COLORS[CATEGORY_ORDER.index(c)] for c in present])
    ax.set_title("Share of hours per European AQI category")
    ax.set_xlabel("City")
    ax.set_ylabel("Share of hours, %")
    ax.legend(title="AQI category", bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.setp(ax.get_xticklabels(), rotation=0)
    figures["fig5_aqi_category_share.png"] = save(fig, "fig5_aqi_category_share.png")

    # 6. correlation of pollutants ---------------------------------------------------------
    correlation = df[config.NUMERIC_COLUMNS].corr()
    fig, ax = plt.subplots(figsize=(8, 6.5))
    sns.heatmap(correlation, annot=True, fmt=".2f", cmap="coolwarm", vmin=-1, vmax=1, ax=ax)
    ax.set_title("Correlation between pollutants and European AQI")
    figures["fig6_correlation.png"] = save(fig, "fig6_correlation.png")
    return figures


def build_findings(df: pd.DataFrame, corr: pd.DataFrame) -> dict:
    """Findings are computed from the data - nothing is typed in by hand."""
    findings = {}
    by_city = df.groupby("city")["pm2_5"].mean().sort_values(ascending=False)
    top, bottom = by_city.index[0], by_city.index[-1]
    ratio = by_city.iloc[0] / by_city.iloc[-1] if by_city.iloc[-1] else float("nan")
    over = by_city[by_city > config.WHO_PM25_24H].index.tolist()
    findings["fig1"] = (
        f"{top} has the highest mean PM2.5 ({by_city.iloc[0]:.1f} µg/m³) and {bottom} the lowest ({by_city.iloc[-1]:.1f} µg/m³); "
        f"the difference is a factor of {ratio:.1f}. "
        + (f"Mean PM2.5 is above the WHO 24-hour guideline of {config.WHO_PM25_24H:.0f} µg/m³ in: {', '.join(over)}."
           if over else f"No city has a mean PM2.5 above the WHO guideline of {config.WHO_PM25_24H:.0f} µg/m³."))

    daily = df.groupby(["observed_date", "city"])["pm2_5"].mean().unstack("city")
    worst_day = daily.mean(axis=1).idxmax()
    best_day = daily.mean(axis=1).idxmin()
    exceed_days = (daily > config.WHO_PM25_24H).sum().sort_values(ascending=False)
    findings["fig2"] = (
        f"Averaged over all cities the most polluted day is {worst_day} ({daily.mean(axis=1).max():.1f} µg/m³) and the cleanest is "
        f"{best_day} ({daily.mean(axis=1).min():.1f} µg/m³). The daily mean exceeded the WHO guideline on "
        f"{int(exceed_days.iloc[0])} of {len(daily)} days in {exceed_days.index[0]}, the most among the cities.")

    hourly = df.groupby("hour")["pm2_5"].mean()
    peak_hour, low_hour = int(hourly.idxmax()), int(hourly.idxmin())
    by_period = df.groupby("day_period")["pm2_5"].mean().sort_values(ascending=False)
    findings["fig3"] = (
        f"Across all cities PM2.5 is highest around {peak_hour:02d}:00 ({hourly.max():.1f} µg/m³) and lowest around {low_hour:02d}:00 "
        f"({hourly.min():.1f} µg/m³). By part of the day, '{by_period.index[0]}' is the most polluted ({by_period.iloc[0]:.1f} µg/m³) "
        f"and '{by_period.index[-1]}' the least ({by_period.iloc[-1]:.1f} µg/m³).")

    spread = df.groupby("city")["pm2_5"].agg(["median", "std", "max"])
    widest = spread["std"].idxmax()
    findings["fig4"] = (
        f"{widest} shows the widest spread of hourly values (std {spread.loc[widest, 'std']:.1f} µg/m³, maximum {spread.loc[widest, 'max']:.1f} µg/m³). "
        f"The median PM2.5 ranges from {spread['median'].min():.1f} to {spread['median'].max():.1f} µg/m³ between the cities.")

    poor_or_worse = df.assign(bad=df["aqi_category"].isin(["Poor", "Very poor", "Extremely poor"])).groupby("city")["bad"].mean() * 100
    findings["fig5"] = (
        f"The share of hours with European AQI 'Poor' or worse is largest in {poor_or_worse.idxmax()} ({poor_or_worse.max():.1f} % of hours) "
        f"and smallest in {poor_or_worse.idxmin()} ({poor_or_worse.min():.1f} %).")

    pm_corr = corr["pm2_5"].drop("pm2_5").dropna()
    strongest = pm_corr.abs().idxmax()
    findings["fig6"] = (
        f"PM2.5 is most strongly correlated with {strongest} (r = {pm_corr[strongest]:.2f}); its correlation with PM10 is "
        f"{corr.loc['pm2_5', 'pm10']:.2f}, with the European AQI {corr.loc['pm2_5', 'european_aqi']:.2f}.")

    exceed_share = df.groupby("city")["pm25_exceeds_who"].mean() * 100
    findings["conclusions"] = [
        f"Highest average PM2.5: {top} ({by_city.iloc[0]:.1f} µg/m³); lowest: {bottom} ({by_city.iloc[-1]:.1f} µg/m³).",
        f"Share of hours above the WHO 24-hour PM2.5 guideline: "
        + ", ".join(f"{c} {v:.0f} %" for c, v in exceed_share.sort_values(ascending=False).items()) + ".",
        f"Typical daily rhythm: peak near {peak_hour:02d}:00, minimum near {low_hour:02d}:00.",
        f"Strongest relation of PM2.5 to another variable: {strongest} (r = {pm_corr[strongest]:.2f}).",
    ]
    return findings


def run() -> int:
    if not config.DB_PATH.exists():
        print(f"[analysis] ERROR {config.DB_PATH} not found - run the loader first", flush=True)
        return 1
    df = load_from_database()
    if df.empty:
        print("[analysis] ERROR the measurements table is empty", flush=True)
        return 1
    print(f"[analysis] loaded {len(df)} rows from SQLite", flush=True)

    describe = df[config.NUMERIC_COLUMNS].describe().round(2)
    describe.to_csv(config.DATA_DIR / "eda_describe.csv", encoding="utf-8")
    categorical = {col: df[col].value_counts().to_dict() for col in ("city", "region", "day_period", "aqi_category")}
    grouped = df.groupby("city")[config.NUMERIC_COLUMNS].mean().round(2)
    grouped.to_csv(config.DATA_DIR / "eda_grouped_by_city.csv", encoding="utf-8")
    corr = df[config.NUMERIC_COLUMNS].corr().round(3)

    figures = make_figures(df)
    findings = build_findings(df, df[config.NUMERIC_COLUMNS].corr())

    summary.write_json(config.EDA_SUMMARY, {
        "shape": list(df.shape),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "missing_values": {c: int(v) for c, v in df.isna().sum().items() if v},
        "descriptive_statistics": describe.reset_index().rename(columns={"index": "statistic"}).to_dict("records"),
        "categorical_counts": categorical,
        "grouped_means_by_city": grouped.reset_index().to_dict("records"),
        "correlation_with_pm2_5": corr["pm2_5"].to_dict(),
        "figures": list(figures),
        "findings": findings,
    })
    summary.update("analysis", {"rows_analysed": int(len(df)), "figures": len(figures)})
    for line in findings["conclusions"]:
        print(f"[analysis] finding: {line}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
