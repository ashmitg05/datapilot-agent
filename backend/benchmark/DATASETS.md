# Benchmark datasets

| Dataset dir | Table | Shape | Source | License |
|---|---|---|---|---|
| `retail` | `retail` | Sales/orders, ~540k invoice lines, Dec 2010 – Dec 2011 | [UCI Online Retail](https://archive.ics.uci.edu/dataset/352/online+retail) (Chen, 2015) | CC BY 4.0 |
| `bikes` | `bike_day` | Daily time series, 731 days, 2011–2012 | [UCI Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset) (Fanaee-T, 2013) | CC BY 4.0 |
| `nyc311` | `complaints` | Messy real-world: NYC 311 requests, 2023-01-01 to 2023-01-07 | [NYC Open Data 311](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9) | NYC Open Data Terms of Use (free reuse, no warranty) |

Why these three: one wide transactional table with returns and missing customer IDs, one clean
time series with coded categories, and one messy operational table (missing boroughs,
"Unspecified" values, inconsistent city casing, closed-before-created timestamps).

Notes:
- `retail`: columns renamed to snake_case. Revenue = `quantity * unit_price`. Invoice numbers
  starting with `C` are cancellations (negative quantities).
- `bike_day`: original columns. `temp`, `atemp`, `hum`, `windspeed` are normalized (temp in °C =
  temp × 41). `season` codes are documented inconsistently upstream, which is why the "summer"
  question is in the ambiguous category.
- `complaints`: a fixed one-week snapshot. The live API changes, so `data/manifest.json`
  records sha256 hashes and gold answers are only valid for that snapshot.

Verify each license on the source page before publishing the repo; record the access date here.
