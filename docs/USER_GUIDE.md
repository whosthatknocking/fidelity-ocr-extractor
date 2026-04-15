# User Guide

## Extract screenshots

1. Put Fidelity Trader+ monitoring screenshots into `input/`.
2. Run:

```bash
python3 main.py extractor
```

The extractor creates CSV files in `output/`. If a matching output file already exists for a PNG, that PNG is skipped.

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
