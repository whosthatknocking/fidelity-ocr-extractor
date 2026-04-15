#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import threading
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = REPO_ROOT / "viewer_static"
OUTPUT_DIR = REPO_ROOT / "output"
README_PATH = REPO_ROOT / "README.md"
CSV_PATTERN = "positions_monitoring_*.csv"


def discover_csv_files() -> list[Path]:
    return sorted(
        OUTPUT_DIR.glob(CSV_PATTERN),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def resolve_csv_path(csv_name: str | None = None) -> Path:
    files = discover_csv_files()
    if not files:
        raise FileNotFoundError("No CSV files were found in the output directory.")
    if not csv_name:
        return files[0]
    candidate = OUTPUT_DIR / csv_name
    if candidate.exists() and candidate.is_file() and candidate.name.startswith("positions_monitoring_"):
        return candidate
    raise FileNotFoundError(f"CSV file not found: {csv_name}")


def load_csv_rows(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def read_reference_markdown() -> str:
    return README_PATH.read_text(encoding="utf-8")


def normalize_number(text: str | None) -> float | None:
    if text is None:
        return None
    cleaned = str(text).strip()
    if not cleaned:
        return None
    multiplier = 1.0
    if cleaned.endswith("M"):
        multiplier = 1_000_000.0
        cleaned = cleaned[:-1]
    cleaned = cleaned.replace("$", "").replace("%", "").replace(",", "").replace(" ", "")
    cleaned = re.sub(r"[^0-9.+-]", "", cleaned)
    if cleaned in {"", "+", "-", ".", "+.", "-."}:
        return None
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def infer_columns(fieldnames: list[str], rows: list[dict[str, str]]) -> list[dict[str, object]]:
    columns = []
    for fieldname in fieldnames:
        sample_values = [row.get(fieldname, "") for row in rows[:20]]
        numeric_hits = sum(1 for value in sample_values if normalize_number(value) is not None)
        columns.append(
            {
                "name": fieldname,
                "description": fieldname.replace("_", " "),
                "is_numeric": numeric_hits >= max(1, len(sample_values) // 2),
            }
        )
    return columns


def format_timestamp_from_name(file_name: str) -> str:
    match = re.match(r"^positions_monitoring_(\d{8}T\d{6}[+-]\d{4})\.csv$", file_name)
    if not match:
        return file_name
    stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S%z")
    return stamp.isoformat(timespec="seconds")


def build_dataset_cards(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if not rows:
        return []

    equities = sum(1 for row in rows if row.get("instrument_type") == "equity")
    options = sum(1 for row in rows if row.get("instrument_type") == "option")
    unique_symbols = len({row.get("symbol", "") for row in rows if row.get("symbol")})
    created_at = rows[0].get("created_at", "")
    return [
        {"name": "Schema", "value": rows[0].get("schema_name", "monitoring"), "description": "Fixed Fidelity monitoring schema."},
        {"name": "Positions", "value": str(len(rows)), "description": "Number of extracted position rows in the selected CSV."},
        {"name": "Equities", "value": str(equities), "description": "Rows classified as stock or ETF positions."},
        {"name": "Options", "value": str(options), "description": "Rows classified as option positions."},
        {"name": "Unique Symbols", "value": str(unique_symbols), "description": "Count of distinct symbols in the selected snapshot."},
        {"name": "Screenshot Time", "value": created_at, "description": "Creation time derived from the source PNG."},
    ]


def build_overview(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        symbol = row.get("symbol", "") or "Unknown"
        grouped.setdefault(symbol, []).append(row)

    summary_rows: list[dict[str, object]] = []
    for symbol, group in sorted(grouped.items()):
        equities = [row for row in group if row.get("instrument_type") == "equity"]
        options = [row for row in group if row.get("instrument_type") == "option"]
        total_gl = sum(normalize_number(row.get("total_gl")) or 0.0 for row in group)
        quantity_values = [normalize_number(row.get("quantity")) for row in group]
        summary_rows.append(
            {
                "symbol": symbol,
                "row_count": len(group),
                "equity_rows": len(equities),
                "option_rows": len(options),
                "expiration_count": len({row.get("expiration") for row in options if row.get("expiration")}),
                "latest_last": next((row.get("last", "") for row in equities if row.get("last")), group[0].get("last", "")),
                "net_quantity": sum(value or 0.0 for value in quantity_values),
                "total_gl": total_gl,
                "descriptions": ", ".join(sorted({row.get("description", "") for row in group if row.get("description")})),
            }
        )
    return summary_rows


def build_data_payload(csv_name: str | None) -> dict[str, object]:
    csv_path = resolve_csv_path(csv_name)
    fieldnames, rows = load_csv_rows(csv_path)
    modified_at = datetime.fromtimestamp(csv_path.stat().st_mtime).isoformat(timespec="seconds")
    return {
        "selected_file": csv_path.name,
        "display_name": format_timestamp_from_name(csv_path.name),
        "row_count": len(rows),
        "columns": infer_columns(fieldnames, rows),
        "rows": rows,
        "freshness_summary": {
            "file_modified_at": modified_at,
            "source_created_at": rows[0].get("created_at", "") if rows else "",
        },
        "dataset_cards": build_dataset_cards(rows),
    }


def build_files_payload() -> dict[str, object]:
    files = []
    for path in discover_csv_files():
        files.append(
            {
                "name": path.name,
                "label": format_timestamp_from_name(path.name),
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return {"files": files}


class ViewerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/files":
            self._send_json(build_files_payload())
            return
        if parsed.path == "/api/data":
            requested_file = parse_qs(parsed.query).get("file", [None])[0]
            self._send_json(build_data_payload(requested_file))
            return
        if parsed.path == "/api/overview":
            requested_file = parse_qs(parsed.query).get("file", [None])[0]
            payload = build_data_payload(requested_file)
            self._send_json(
                {
                    "selected_file": payload["selected_file"],
                    "rows": build_overview(payload["rows"]),
                }
            )
            return
        if parsed.path == "/api/reference":
            self._send_json({"markdown": read_reference_markdown()})
            return

        return super().do_GET()

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browse extracted Fidelity monitoring CSV files.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="Open the viewer in the default browser.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), ViewerHandler)
    url = f"http://{args.host}:{args.port}"
    if args.open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    print(f"Viewer listening on {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
