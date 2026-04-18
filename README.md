# Fidelity OCR Extractor

[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/whosthatknocking/fidelity-ocr-extractor/actions/workflows/lint.yml/badge.svg)](https://github.com/whosthatknocking/fidelity-ocr-extractor/actions/workflows/lint.yml)
[![codecov](https://codecov.io/gh/whosthatknocking/fidelity-ocr-extractor/branch/main/graph/badge.svg)](https://codecov.io/gh/whosthatknocking/fidelity-ocr-extractor)

Extract Fidelity Trader+ positions from monitoring view screenshots and export them as CSV files.

## ⚠️ Status

**This is an experimental prototype.** Screenshot OCR is not recommended for production use due to reliability limitations. Consider using direct data exports, browser DOM extraction, or accessibility APIs instead.

## Features

- Extract positions data from Fidelity Trader+ monitoring screenshots
- Automatic column detection and calibration
- CSV export with structured data
- Support for equity and options positions
- Dark theme compatibility
- Batch processing of PNG screenshots

## Design Approach

This tool uses a multi-stage OCR pipeline to extract structured data from Fidelity Trader+ monitoring screenshots:

**OCR Engine**: Leverages macOS Vision framework (primary) with Tesseract OCR as fallback for text recognition from PNG images.

**Column Detection**: Automatically calibrates column positions by analyzing the header row, allowing flexible screenshot widths without fixed dimensions.

**Data Processing**: Applies schema-specific parsing, text normalization, and validation rules defined in `config.toml` to transform raw OCR output into clean CSV data.

**Quality Gates**: Implements image quality checks and cross-field validation to reject low-confidence extractions rather than producing unreliable data.

## Installation

### Requirements

- Python 3.9+
- macOS (for Vision OCR framework)

### Install from source

```bash
git clone https://github.com/whosthatknocking/fidelity-ocr-extractor.git
cd fidelity-ocr-extractor
pip install -e .[dev]
```

## Usage

### Basic Usage

Place PNG screenshots in the `input/` directory and run:

```bash
python3 main.py extractor
```

Or use the installed script:

```bash
fidelity-ocr-extractor
```

### Output

- Processed screenshots are skipped if their CSV output already exists
- Output files are named `positions_monitoring_<timestamp>.csv`
- CSV contains structured position data with fields like symbol, price, change, etc.

### Directory Structure

```
fidelity-ocr-extractor/
├── input/          # Place PNG screenshots here
├── output/         # Generated CSV files appear here
├── docs/           # Documentation and specifications
├── tests/          # Test suite
└── config.toml     # Extraction configuration
```

## Development

### Setup

```bash
pip install -e .[dev]
```

### Testing

```bash
pytest
```

### Linting

```bash
flake8
```

### Project Structure

- `extract.py` - Core OCR and data extraction logic
- `main.py` - CLI entry point
- `config.toml` - Schema definitions and extraction rules
- `docs/` - Detailed documentation

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure linting passes
6. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
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
fidelity-ocr-extractor extractor
```

## Current Scope

Only the Fidelity Trader+ positions `monitoring` view was explored in this prototype. Screenshots that cannot be extracted reliably are rejected instead of being emitted with low-confidence data.
