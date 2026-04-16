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
- output files are named like `positions_monitoring_<timestamp>.csv`
- `created_at` is derived from the PNG creation time when available
- the extractor calibrates column positions from the detected monitoring header so browser-size changes do not require a fixed screenshot resolution
- screenshots must pass basic quality gates for size, contrast, and recognizable header anchors before extraction runs
- `symbol`, `last`, `change`, `percent_change`, `bid`, `ask`, `volume`, `quantity`, `day_range_low`, and `day_range_high` are required; unsupported screenshots fail clearly instead of writing partial rows
- `week_52_low` and `week_52_high` are extracted when present, but they may be blank on screenshots where that range is not visible
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
