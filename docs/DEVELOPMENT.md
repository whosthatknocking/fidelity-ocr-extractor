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
- Private screenshots and generated CSVs are intentionally ignored by git.
