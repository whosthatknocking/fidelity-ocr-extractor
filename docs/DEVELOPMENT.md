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
- Image quality gates should fail early on screenshots that are too small, too low-contrast, or missing enough header anchors.
- Keep raw OCR collection separate from normalization and validation so debugging and regression fixtures stay deterministic.
- Private screenshots and generated CSVs are intentionally ignored by git.
