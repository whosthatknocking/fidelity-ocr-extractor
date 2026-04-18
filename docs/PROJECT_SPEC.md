# Project Spec

## Purpose

`fidelity-ocr-extractor` is an archived prototype that explored converting Fidelity Trader+ positions screenshots from the fixed `monitoring` layout into CSV files.

This spec remains useful as a record of the schema and validation assumptions, but the repository should not be treated as a production-ready extraction system.

## Supported Scope

- Input format: PNG screenshots in `input/`
- Supported Fidelity view: positions `monitoring`
- Output format: CSV files in `output/`
- OCR backend: local macOS Vision or local `tesseract` fallback
- Project status: experimental reference, not a production extraction path

## Explicit Non-Scope

- other Fidelity views
- portfolio analytics beyond the extracted table
- trading automation
- cloud OCR services

## Product Contracts

- Only PNG files inside `input/` are eligible for extraction.
- Output filenames must start with `positions_monitoring_` and use only the derived timestamp as the suffix.
- The CSV schema is fixed to the current monitoring fields emitted by `extract.py`.
- The monitoring extraction contract, including required CSV fields and canonical header labels, lives in `config.toml`.
- `created_at` must come from the PNG creation time when available.
- Re-running the extractor must check every file in `input/` and skip only when the deterministic output file already exists.
- Extraction must adapt to browser-size and screenshot-resolution changes by calibrating column positions from the detected monitoring header row.
- Extraction must reject screenshots that fail basic image quality gates or do not expose all monitoring headers.
- Header detection may normalize bounded OCR mistakes, but it must still map every header position to the fixed canonical monitoring label set.
- Extraction must separate raw OCR collection from normalization and validation.
- Symbol extraction must prefer strict monitoring shapes: `TICKER` for equities and `TICKER <strike> Call|Put` for options.
- Option expirations must be emitted only for option rows, in `Mon DD YYYY` form. Equity rows must leave `expiration` blank.
- Decorative badges such as the purple `M` and green `E` must be ignored during parsing.
- Required monitoring fields are defined by `monitoring.required_fields` in `config.toml`; the current required set is `symbol`, `last`, `change`, `percent_change`, `bid`, `ask`, and `quantity`.
- All monitoring header labels are required and are defined by `monitoring.required_header_keys` plus `monitoring.headers` in `config.toml`.
- `description`, `day_range_low`, `day_range_high`, `week_52_low`, `week_52_high`, `avg_cost`, `total_gl`, and `percent_total_gl` remain part of the CSV schema but may be blank on individual rows even though their headers are always required.

## Outcome Note

The experiment showed that screenshot OCR, even with schema-specific repair and validation, is not stable enough to be considered a product-grade solution for this workflow. Future work should prefer semantic extraction from the live UI or an upstream export source over continued OCR tuning.
