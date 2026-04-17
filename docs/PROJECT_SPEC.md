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
- The monitoring extraction contract, including required CSV fields and canonical header labels, lives in `fidelity_extractor.toml`.
- `created_at` must come from the PNG creation time when available.
- Re-running the extractor must check every file in `input/` and skip only when the deterministic output file already exists.
- Extraction must adapt to browser-size and screenshot-resolution changes by calibrating column positions from the detected monitoring header row.
- Extraction must reject screenshots that fail basic image quality gates or do not expose all monitoring headers.
- Header detection may normalize bounded OCR mistakes, but it must still map every header position to the fixed canonical monitoring label set.
- Extraction must separate raw OCR collection from normalization and validation.
- Required monitoring fields are defined by `monitoring.required_fields` in `fidelity_extractor.toml`; the current required set is `symbol`, `last`, `change`, `percent_change`, `bid`, `ask`, `volume`, and `quantity`.
- All monitoring header labels are required and are defined by `monitoring.required_header_keys` plus `monitoring.headers` in `fidelity_extractor.toml`.
- `day_range_low`, `day_range_high`, `week_52_low`, `week_52_high`, `avg_cost`, `total_gl`, and `percent_total_gl` remain part of the CSV schema but may be blank on individual rows even though their headers are always required.
