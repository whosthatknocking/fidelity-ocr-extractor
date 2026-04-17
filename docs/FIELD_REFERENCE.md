# Field Reference

- `schema_name`: fixed schema identifier. Current value is `monitoring`.
- `image_file`: source PNG filename.
- `created_at`: timestamp derived from the PNG creation time when available.
- `symbol`: extracted ticker symbol in one of these forms: `TICKER` or `TICKER <strike> Call|Put`.
- `instrument_type`: `equity` or `option`.
- `description`: optional free-text field. The extractor may leave it blank to avoid exporting noisy OCR.
- `expiration`: option expiration string in `Mon DD YYYY` form for option rows only. Equity rows leave this blank.
- `last`: last traded price as a dollar amount.
- `change`: absolute change value as a signed dollar amount.
- `percent_change`: percent move column as a signed percentage.
- `bid`: bid price.
- `ask`: ask price.
- `volume`: volume column.
- `day_range_low`: lower bound of day range.
- `day_range_high`: upper bound of day range.
- `week_52_low`: lower bound of 52-week range.
- `week_52_high`: upper bound of 52-week range.
- `avg_cost`: average cost basis column.
- `quantity`: quantity column as an integer and it may be negative.
- `total_gl`: total gain/loss column as a signed dollar amount.
- `percent_total_gl`: total gain/loss percent column as a signed percentage.

Required for every extracted monitoring row:

- `symbol`
- `last`
- `change`
- `percent_change`
- `bid`
- `ask`
- `volume`
- `quantity`

Optional on individual rows even though their headers are required:

- `day_range_low`
- `day_range_high`
- `week_52_low`
- `week_52_high`
- `avg_cost`
- `total_gl`
- `percent_total_gl`
