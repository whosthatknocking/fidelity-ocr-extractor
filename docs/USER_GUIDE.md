# User Guide

## Extract screenshots

1. Put Fidelity Trader+ monitoring screenshots into `input/`.
2. Run:

```bash
python3 main.py extractor
```

The extractor creates CSV files in `output/`. If a matching output file already exists for a PNG, that PNG is skipped.

Notes:

- The parser calibrates itself from the monitoring header row, so the browser window can be wider or narrower without needing a single fixed screenshot size.
- Required headers are strict: `Symbol`, `Last`, `Change`, `% Change`, `Bid`, `Ask`, `Volume`, `Day range`, `52-week range`, `Avg. cost`, `Quantity`, `$ Total G/L`, and `% Total G/L` must be present.
- Header OCR is normalized back to that fixed label set, so minor OCR spelling mistakes in the header text do not automatically fail extraction.
- The required CSV fields are configured in `config.toml`; by default they are `symbol`, `last`, `change`, `percent_change`, `bid`, `ask`, and `quantity`.
- `Day range`, `52-week range`, `Avg. cost`, `$ Total G/L`, and `% Total G/L` headers must be present, but their row values may be blank.
- Screenshots that fail image quality gates or cross-field validation are rejected instead of being exported with low-confidence rows.

## Install dependencies

Runtime only:

```bash
python3 -m pip install -e .
```

Runtime plus test tooling:

```bash
python3 -m pip install -e ".[dev]"
```

## Browse extracted CSVs

Run:

```bash
python3 main.py viewer
python3 main.py viewer --open
```

The viewer opens a local HTTP UI for:

- selecting an extracted CSV snapshot
- sorting and filtering the dataset table
- paging through rows
- opening row details
- reading the field reference for the current CSV schema

## Install as a package

```bash
python3 -m pip install -e .
```

Then use:

```bash
fidelity-extractor extractor
fidelity-extractor viewer
fidelity-extractor viewer --open
```
