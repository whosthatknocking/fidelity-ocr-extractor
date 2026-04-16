# Project Spec

## Purpose

`fidelity-extractor` converts Fidelity Trader+ positions screenshots from the fixed `monitoring` layout into CSV files and serves a local inspection viewer.

## Supported Scope

- Input format: PNG screenshots in `input/`
- Supported Fidelity view: positions `monitoring`
- Output format: CSV files in `output/`
- Viewer scope: local browsing and inspection of generated CSV files

## Explicit Non-Scope

- other Fidelity views
- portfolio analytics beyond the extracted table
- trading automation
- cloud OCR services

## Product Contracts

- Only PNG files inside `input/` are eligible for extraction.
- Output filenames must start with `positions_monitoring_` and use only the derived timestamp as the suffix.
- The CSV schema is fixed to the current monitoring fields emitted by `extract.py`.
- Exported rows include OCR review metadata: `row_confidence` and `review_notes`.
- `created_at` must come from the PNG creation time when available.
- Re-running the extractor must check every file in `input/` and skip only when the deterministic output file already exists.
- Extraction must adapt to browser-size and screenshot-resolution changes by calibrating column positions from the detected monitoring header row.
- `symbol`, `last`, `change`, `percent_change`, `bid`, `ask`, `volume`, `quantity`, `day_range_low`, and `day_range_high` are required monitoring fields and must not be emitted as blank values.
- `week_52_low` and `week_52_high` remain part of the CSV schema but may be blank when that range is not visible in the screenshot.
