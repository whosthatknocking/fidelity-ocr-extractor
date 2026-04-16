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
- Missing required monitoring fields should raise a clear extraction error instead of silently producing partial rows.
- `week_52_low` and `week_52_high` are optional because some screenshots do not expose the 52-week range.
- Rows carry `row_confidence` and `review_notes` so repaired OCR output is inspectable in the CSV and viewer.
- Private screenshots and generated CSVs are intentionally ignored by git.
