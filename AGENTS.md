# AGENTS.md

This file gives project-specific guidance to AI agents working in this repository.

## Project Context

- Project: `fidelity-extractor`
- Purpose: extract the Fidelity Trader+ positions monitoring view from PNG screenshots into CSV files
- Runtime: Python `3.9+` and macOS Vision OCR via `swift`
- Main entrypoints:
  - `python3 extract.py` for screenshot-to-CSV extraction

## Source of Truth

When behavior, naming, or scope is unclear, use these files in this order:

1. `docs/PROJECT_SPEC.md`
2. `docs/USER_GUIDE.md`
3. `docs/FIELD_REFERENCE.md`
4. `docs/DEVELOPMENT.md`
5. `README.md`
6. `AGENTS.md`
7. `extract.py`

Keep those files aligned with the implementation. If you change CSV fields, extraction rules, file naming, or input/output layout, update the docs in the same task.

## Architecture Map

- `extract.py`
  - scans `input/` for PNG files
  - uses macOS Vision OCR
  - maps OCR boxes into the fixed `monitoring` schema
  - writes CSV files under `output/`
- `main.py`
  - top-level subcommand entrypoint
  - dispatches to the extractor

## Non-Negotiable Design Rules

- Support only the Fidelity Trader+ positions `monitoring` view unless the docs are updated deliberately.
- Read PNG input only from `input/`.
- Write extracted CSV output only to `output/`.
- Output filenames must start with `positions_monitoring_` and contain only the timestamp suffix.
- Process every file in `input/` on each run, but skip files whose deterministic output CSV already exists.
- Do not silently invent field meanings when OCR is ambiguous. Leave uncertain values as-is or blank rather than mapping misleading values.

## Extraction Conventions

- Preserve the current CSV schema unless the documentation changes with it.
- Keep the monitoring extraction contract in `config.toml` aligned with the implementation and docs.
- Derive `created_at` from the PNG creation time when available, otherwise fall back to modification time.
- Keep OCR cleanup rules targeted and reversible. Prefer narrow fixes for known Fidelity screenshot artifacts over broad text mutation.
- If a screenshot does not match the supported monitoring layout, fail clearly or document the limitation rather than pretending it parsed correctly.

- Keep exported CSVs under `output/`.

## Error Handling and Stability

- Raise clear, project-appropriate errors for OCR, file-discovery, and parsing failures.
- Do not leak confusing raw OCR output to users when a clearer project-level failure is possible.
- Favor deterministic local behavior over network-dependent services.

## Testing Expectations

Run the smallest relevant verification first, then broaden if needed.

- Main checks:
  - `python3 extract.py`

Testing guidance:

- Add or update focused verification whenever extraction mapping or output shape changes.
- Prefer offline, deterministic checks by default.
- If OCR quality limits a value, say so explicitly and note what still needs manual review.

## Documentation Expectations

Update `README.md` when any of these change:

- CSV field names or meanings
- input/output directory rules
- extraction naming rules
- supported Fidelity views

Keep the `docs/` files aligned with implementation when changing contracts or user-facing behavior.

## Practical Workflow

1. Read the extractor code first.
2. Make the smallest coherent change that keeps extraction and CSV shape aligned.
3. Update docs with user-facing behavior changes.
4. Run targeted verification and state what was actually checked.

## Good Changes

- tightening OCR-to-column mapping for the monitoring schema
- improving deterministic skip behavior for already extracted PNGs
- updating docs so they match the actual extractor behavior

## Bad Changes

- reading screenshots from outside `input/`
- writing ad hoc files outside `output/`
- adding undocumented CSV columns casually
- claiming support for non-monitoring Fidelity views without implementation
