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
- If the screenshot does not expose the required monitoring columns clearly enough to recover `symbol`, `last`, `change`, `% change`, `bid`, `ask`, `volume`, `quantity`, and `day range`, extraction fails instead of writing partial data.
- `52-week range` is extracted when visible, but missing `week_52_low` and `week_52_high` do not fail extraction.
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
