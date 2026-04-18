# Fidelity Extractor

This project extracts Fidelity Trader+ positions from the fixed `monitoring` screenshot view and writes them as CSV files.

## Objective

Fidelity does not provide a simple export path or public API for this Trader+ monitoring view. This script exists to close that gap: turn the on-screen positions table into a structured CSV so the data can be reviewed, archived, and worked with outside the Fidelity UI.

## Layout

- `input/`: source PNG files to process
- `output/`: extracted CSV files
- `docs/`: project contract and usage notes
- `viewer_static/`: local viewer assets

## Extract CSVs

```bash
python3 main.py extractor
```

Behavior:

- only PNG files under `input/` are considered
- every file in `input/` is checked on every run
- if the deterministic output CSV already exists, that PNG is skipped
- unsupported or invalid screenshots are reported as per-file failures and do not abort the rest of the batch
- output files are named like `positions_monitoring_<timestamp>.csv`
- `created_at` is derived from the PNG creation time when available
- extraction rules for the monitoring schema live in [config.toml](config.toml)
- the extractor calibrates column positions from the detected monitoring header so browser-size changes do not require a fixed screenshot resolution
- monitoring extraction is schema-driven rather than generic: once the header is anchored, each column is parsed with field-specific rules for symbols, signed money, signed percentages, integers, and ranges
- OCR runs locally and prefers `tesseract` when it is installed; otherwise the extractor falls back to the existing macOS Vision `swift` path
- for dark-theme screenshots, left-side symbol and expiration OCR also tries inverted black-on-white variants instead of relying on one fixed color treatment
- screenshots must pass basic quality gates for size, contrast, and exact monitoring-header OCR before extraction runs
- all monitoring headers must be present in the screenshot header: `Symbol`, `Last`, `Change`, `% Change`, `Bid`, `Ask`, `Volume`, `Day range`, `52-week range`, `Avg. cost`, `Quantity`, `$ Total G/L`, and `% Total G/L`
- header detection maps OCR output back to that fixed canonical header set with position-aware matching, so minor OCR slips do not invalidate an otherwise correct screenshot
- symbol extraction is intentionally strict: equity rows export `TICKER`, option rows export `TICKER <strike> Call|Put`, and option expirations are normalized from the adjacent date text
- option expirations must resolve to canonical `Mon DD YYYY` text; raw OCR lines that merely contain a month token are rejected rather than exported
- icon badges such as the purple `M` or green `E` are treated as noise and ignored during symbol parsing
- the required CSV fields are configured in `config.toml`; by default they are `symbol`, `last`, `change`, `percent_change`, `bid`, `ask`, and `quantity`
- `description` is optional and may be blank when left-side OCR is noisy; `day_range_low`, `day_range_high`, `week_52_low`, `week_52_high`, `avg_cost`, `total_gl`, and `percent_total_gl` may still be blank on individual rows even when their headers are present
- extraction separates raw OCR collection from normalization and validation, and ambiguous rows fail instead of being exported as low confidence
- `input/` and generated CSV files are gitignored to reduce accidental commits of private data

## View CSVs

```bash
python3 main.py viewer
python3 main.py viewer --open
```

The viewer serves the CSV files in `output/` and provides:

- file picker for extracted snapshots
- sortable, filterable dataset table
- pagination
- row detail modal
- reference tab backed by the field reference document

## Packaging

Install runtime dependencies with:

```bash
python3 -m pip install -e .
```

For the local OCR fallback used on the sample screenshots in this repo, install:

```bash
brew install tesseract
```

Install runtime and test dependencies with:

```bash
python3 -m pip install -e ".[dev]"
```

Console scripts:

```bash
fidelity-extractor extractor
fidelity-extractor viewer
fidelity-extractor viewer --open
```

## Current Scope

Only the Fidelity Trader+ positions `monitoring` view is supported right now. Screenshots that cannot be extracted reliably are rejected instead of being emitted with low-confidence data.
