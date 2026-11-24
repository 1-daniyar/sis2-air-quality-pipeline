# Data dictionary

Source: Open-Meteo Air Quality API (`https://air-quality-api.open-meteo.com/v1/air-quality`), hourly values for four cities.
One record = one city at one hour.

## Raw / validated fields (`data/raw_data.csv`, Kafka messages)

| Field | Type | Kind | Description |
|---|---|---|---|
| record_id | text | identifier | `<city key>_<YYYY-MM-DDTHH:MM>`, unique, e.g. `almaty_2026-11-10T00:00` |
| city | text | categorical | Almaty, Astana, Shymkent, Karaganda |
| region | text | categorical | Administrative unit of the city |
| latitude, longitude | float | numerical | Centre of the model grid cell returned by the API (degrees) |
| time | text (ISO 8601) | date/time | Local time (Asia/Almaty, UTC+5) of the hourly value |
| pm10 | float | numerical | Particulate matter < 10 µm, µg/m³ |
| pm2_5 | float | numerical | Particulate matter < 2.5 µm, µg/m³ |
| carbon_monoxide | float | numerical | CO, µg/m³ |
| nitrogen_dioxide | float | numerical | NO2, µg/m³ |
| sulphur_dioxide | float | numerical | SO2, µg/m³ |
| ozone | float | numerical | O3, µg/m³ |
| european_aqi | float | numerical | European Air Quality Index (max of the pollutant sub-indices) |
| collected_at | text (UTC) | date/time | When the pipeline downloaded the record |

Missing measurements are `null` (JSON) / empty (CSV).

## Fields added by cleaning (`data/cleaned_data.csv`, table `measurements`)

| Field | Type | Description |
|---|---|---|
| date | text | Calendar date of `time` |
| hour | integer 0-23 | Hour of the day |
| day_period | text | night (0-5), morning (6-11), afternoon (12-17), evening (18-23) |
| is_weekend | integer 0/1 | 1 for Saturday/Sunday |
| aqi_category | text | European AQI band: Good (≤20), Fair (≤40), Moderate (≤60), Poor (≤80), Very poor (≤100), Extremely poor (>100), Unknown |
| pm25_exceeds_who | integer 0/1 | 1 if hourly PM2.5 > 15 µg/m³ (WHO 2021 24-hour guideline value, used as an indicative threshold) |
| pm2_5_log | float | `log1p(pm2_5)` |
| pm_ratio | float | `pm2_5 / pm10` |
| pm2_5_zscore | float | Z-score of PM2.5 inside the city |

## SQLite tables (`data/output.db`)

* `cities(city_id PK, city_name UNIQUE, region, latitude, longitude)`
* `measurements(record_id PK, city_id FK -> cities, observed_at, observed_date, hour, day_period, is_weekend, pm10, pm2_5, carbon_monoxide, nitrogen_dioxide, sulphur_dioxide, ozone, european_aqi, aqi_category, pm25_exceeds_who, pm2_5_log, pm_ratio, pm2_5_zscore, collected_at, loaded_at, UNIQUE(city_id, observed_at))`
