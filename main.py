#!/usr/bin/env python3

"""Compatibility entrypoint for the extractor and local viewer."""

from __future__ import annotations

import argparse

import extract
import viewer as viewer_module

SCHEMA_NAME = extract.SCHEMA_NAME
INPUT_DIR = extract.INPUT_DIR
OUTPUT_DIR = extract.OUTPUT_DIR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fidelity Trader+ screenshot extractor and CSV viewer."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extractor_parser = subparsers.add_parser(
        "extractor",
        help="Extract PNG screenshots from input/ into monitoring CSV files.",
    )
    extractor_parser.add_argument("--input-dir", default=str(INPUT_DIR))
    extractor_parser.add_argument("--output-dir", default=str(OUTPUT_DIR))

    viewer_parser = subparsers.add_parser(
        "viewer",
        help="Run the local HTTP viewer for extracted monitoring CSV files.",
    )
    viewer_parser.add_argument("--host", default="127.0.0.1")
    viewer_parser.add_argument("--port", type=int, default=8000)
    viewer_parser.add_argument("--open", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "extractor":
        return extract.main(
            ["--input-dir", args.input_dir, "--output-dir", args.output_dir]
        )
    if args.command == "viewer":
        viewer_args = ["--host", args.host, "--port", str(args.port)]
        if args.open:
            viewer_args.append("--open")
        return viewer_module.main(viewer_args)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
