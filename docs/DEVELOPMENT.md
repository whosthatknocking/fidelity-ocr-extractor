# Development

## Runtime requirements

- Python 3.9+
- macOS with `swift` and Vision available

## Main files

- `main.py`: top-level subcommand entrypoint
- `extract.py`: OCR extraction pipeline
- `viewer.py`: local HTTP viewer
- `viewer_static/`: browser assets

## Verification

- `python3 main.py extractor`
- `python3 main.py viewer`

## Notes

- OCR quality depends on screenshot clarity and the fixed Fidelity monitoring layout.
- Column extraction is calibrated from the detected header row rather than assuming one fixed browser width.
- The monitoring extraction contract is loaded from `fidelity_extractor.toml`.
- Missing required monitoring fields should raise a clear extraction error instead of silently producing partial rows.
- `day_range_low`, `day_range_high`, `week_52_low`, `week_52_high`, `avg_cost`, `total_gl`, and `percent_total_gl` are optional at the row-value level even though their headers are mandatory.
- Image quality gates should fail early on screenshots that are too small, too low-contrast, or missing any canonical monitoring header.
- Keep raw OCR collection separate from normalization and validation so debugging and regression fixtures stay deterministic.
- Private screenshots and generated CSVs are intentionally ignored by git.
