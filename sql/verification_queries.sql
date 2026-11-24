-- SQL queries used to verify the contents of data/output.db
-- Each block starts with "-- name: <query_name>" and is executed by src/loader.py.

-- name: total_rows
SELECT COUNT(*) AS total_rows FROM measurements;

-- name: rows_per_city
SELECT c.city_name, COUNT(*) AS rows_count
FROM measurements m JOIN cities c ON c.city_id = m.city_id
GROUP BY c.city_name
ORDER BY c.city_name;

-- name: observation_period
SELECT MIN(observed_at) AS first_hour, MAX(observed_at) AS last_hour, COUNT(DISTINCT observed_date) AS days
FROM measurements;

-- name: duplicate_check_expect_zero_rows
SELECT city_id, observed_at, COUNT(*) AS copies
FROM measurements
GROUP BY city_id, observed_at
HAVING COUNT(*) > 1;

-- name: orphan_rows_expect_zero
SELECT COUNT(*) AS orphan_rows
FROM measurements m LEFT JOIN cities c ON c.city_id = m.city_id
WHERE c.city_id IS NULL;

-- name: null_counts
SELECT SUM(pm10 IS NULL) AS pm10_nulls, SUM(pm2_5 IS NULL) AS pm2_5_nulls,
       SUM(european_aqi IS NULL) AS aqi_nulls, SUM(ozone IS NULL) AS ozone_nulls
FROM measurements;

-- name: average_pm25_per_city
SELECT c.city_name, ROUND(AVG(m.pm2_5), 2) AS avg_pm2_5, ROUND(MAX(m.pm2_5), 2) AS max_pm2_5
FROM measurements m JOIN cities c ON c.city_id = m.city_id
GROUP BY c.city_name
ORDER BY avg_pm2_5 DESC;

-- name: aqi_category_distribution
SELECT aqi_category, COUNT(*) AS rows_count
FROM measurements
GROUP BY aqi_category
ORDER BY rows_count DESC;

-- name: five_most_polluted_hours
SELECT c.city_name, m.observed_at, m.pm2_5, m.european_aqi
FROM measurements m JOIN cities c ON c.city_id = m.city_id
ORDER BY m.pm2_5 DESC
LIMIT 5;
