# Field Reference

- `schema_name`: fixed schema identifier. Current value is `monitoring`.
- `image_file`: source PNG filename.
- `created_at`: timestamp derived from the PNG creation time when available.
- `symbol`: extracted symbol or option contract label from the monitoring table.
- `instrument_type`: `equity` or `option`.
- `description`: equity description when OCR captures it.
- `expiration`: option expiration string when present in the screenshot.
- `last`: last traded price.
- `change`: absolute change value.
- `percent_change`: percent move column.
- `bid`: bid price.
- `ask`: ask price.
- `volume`: volume column.
- `day_range_low`: lower bound of day range.
- `day_range_high`: upper bound of day range.
- `week_52_low`: lower bound of 52-week range.
- `week_52_high`: upper bound of 52-week range.
- `avg_cost`: average cost basis column.
- `quantity`: quantity column as captured from OCR.
- `total_gl`: total gain/loss column.
- `percent_total_gl`: total gain/loss percent column.

Required for every extracted monitoring row:

- `symbol`
- `last`
- `change`
- `percent_change`
- `bid`
- `ask`
- `volume`
- `quantity`
- `day_range_low`
- `day_range_high`
Optional when the 52-week range is not visible in the screenshot:

- `week_52_low`
- `week_52_high`
