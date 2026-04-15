#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parent
INPUT_DIR = REPO_ROOT / "input"
OUTPUT_DIR = REPO_ROOT / "output"
SCHEMA_NAME = "monitoring"
OUTPUT_PREFIX = "positions_monitoring"

VISION_OCR_SWIFT = r"""
import AppKit
import Foundation
import Vision

struct Item: Codable {
    let text: String
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    fputs("missing image path\n", stderr)
    exit(1)
}

let url = URL(fileURLWithPath: arguments[1])
guard let image = NSImage(contentsOf: url) else {
    fputs("could not open image\n", stderr)
    exit(1)
}

var proposedRect = CGRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &proposedRect, context: nil, hints: nil) else {
    fputs("could not convert image\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.minimumTextHeight = 0.004

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])

let items = (request.results ?? []).compactMap { observation -> Item? in
    guard let candidate = observation.topCandidates(1).first else {
        return nil
    }

    let box = observation.boundingBox
    return Item(
        text: candidate.string,
        x: box.minX,
        y: box.minY,
        width: box.width,
        height: box.height
    )
}

let data = try JSONEncoder().encode(items)
FileHandle.standardOutput.write(data)
"""


@dataclass(frozen=True)
class OcrItem:
    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center_y(self) -> float:
        return self.y + (self.height / 2)


COLUMN_RANGES = {
    "last": (0.20, 0.28),
    "change": (0.28, 0.34),
    "percent_change": (0.34, 0.40),
    "bid": (0.40, 0.46),
    "ask": (0.46, 0.52),
    "volume": (0.52, 0.57),
    "day_range_low": (0.57, 0.605),
    "day_range_high": (0.605, 0.635),
    "week_52_low": (0.635, 0.665),
    "week_52_high": (0.665, 0.71),
    "avg_cost": (0.71, 0.79),
    "quantity": (0.79, 0.86),
    "total_gl": (0.86, 0.94),
    "percent_total_gl": (0.94, 1.01),
}

OUTPUT_FIELDS = [
    "schema_name",
    "image_file",
    "created_at",
    "symbol",
    "instrument_type",
    "description",
    "expiration",
    "last",
    "change",
    "percent_change",
    "bid",
    "ask",
    "volume",
    "day_range_low",
    "day_range_high",
    "week_52_low",
    "week_52_high",
    "avg_cost",
    "quantity",
    "total_gl",
    "percent_total_gl",
]

MONTH_FIXES = {
    "Mav": "May",
    "Aua": "Aug",
    "AU": "Aug ",
}

DESCRIPTION_FIXES = {
    "MISROSOFT": "MICROSOFT",
    "COKKORAHONCOM": "CORPORATION COM",
    "CAP STK CLA": "CAP STK CL A",
    "TECHNOLOGIE$": "TECHNOLOGIES",
}

EXACT_TEXT_FIXES = {
    "AU01026": "Aug 21 2026",
    "AU0206": "Aug 21 2026",
    "Aug 0 1026": "Aug 21 2026",
    "May 15 2028": "May 15 2026",
}

CELL_REPAIR_ATTEMPTS = {
    "quantity": [
        (0.77, 0.87, 8, None),
        (0.76, 0.90, 12, 120),
    ],
    "volume": [
        (0.52, 0.57, 10, None),
        (0.52, 0.57, 10, 120),
    ],
    "percent_change": [
        (0.34, 0.40, 10, None),
    ],
    "change": [
        (0.28, 0.34, 10, None),
    ],
    "day_range_low": [
        (0.57, 0.605, 10, None),
    ],
    "day_range_high": [
        (0.605, 0.635, 10, None),
    ],
    "week_52_low": [
        (0.635, 0.665, 10, None),
    ],
    "week_52_high": [
        (0.665, 0.71, 10, None),
    ],
}


def run_vision_ocr(image_path: Path) -> list[OcrItem]:
    result = subprocess.run(
        ["swift", "-", str(image_path)],
        input=VISION_OCR_SWIFT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    return [OcrItem(**item) for item in payload]


def group_rows(items: Iterable[OcrItem], threshold: float = 0.010) -> list[list[OcrItem]]:
    rows: list[list[OcrItem]] = []
    current: list[OcrItem] = []
    current_center: float | None = None

    for item in sorted(items, key=lambda entry: (-entry.center_y, entry.x)):
        if current_center is None or abs(item.center_y - current_center) <= threshold:
            current.append(item)
            current_center = sum(existing.center_y for existing in current) / len(current)
            continue

        rows.append(sorted(current, key=lambda entry: entry.x))
        current = [item]
        current_center = item.center_y

    if current:
        rows.append(sorted(current, key=lambda entry: entry.x))

    return rows


def normalize_spaces(text: str) -> str:
    return " ".join(text.split())


def clean_text(text: str) -> str:
    text = normalize_spaces(text.strip())
    if not text:
        return text

    text = EXACT_TEXT_FIXES.get(text, text)
    text = (
        text.replace("S ", "$ ")
        .replace("+S", "+$")
        .replace("-S", "-$")
        .replace("•", "")
        .replace("Ф", "M")
        .replace("@", "M")
        .replace("©", "")
        .replace("а", "4")
        .replace("á", "4")
        .replace("л", "4")
    )
    text = text.replace("AuR", "Aug").replace("AUR", "Aug")
    text = re.sub(r"(?<=[A-Za-z])\$(?=[A-Za-z])", "S", text)
    text = re.sub(r"([A-Za-z]{3})\s+(\d{1,2})(\d{4})\b", r"\1 \2 \3", text)

    for source, destination in MONTH_FIXES.items():
        text = text.replace(source, destination)

    for source, destination in DESCRIPTION_FIXES.items():
        text = text.replace(source, destination)

    return re.sub(r"\s+", " ", text).strip()


def items_in_range(items: list[OcrItem], left: float, right: float) -> list[OcrItem]:
    return [item for item in items if left <= item.x < right]


def join_items(items: list[OcrItem]) -> str:
    return clean_text(" ".join(item.text for item in sorted(items, key=lambda entry: entry.x)))


def extract_symbol_lines(row: list[OcrItem], threshold: float = 0.012) -> list[str]:
    left_items = [item for item in row if item.x < 0.20]
    if not left_items:
        return []

    lines: list[list[OcrItem]] = []
    for item in sorted(left_items, key=lambda entry: -entry.center_y):
        if not lines:
            lines.append([item])
            continue

        current_center = sum(existing.center_y for existing in lines[-1]) / len(lines[-1])
        if abs(item.center_y - current_center) <= threshold:
            lines[-1].append(item)
        else:
            lines.append([item])

    normalized: list[str] = []
    for line in lines:
        text = clean_text(" ".join(item.text for item in sorted(line, key=lambda entry: entry.x)))
        if text:
            normalized.append(text)
    return normalized


def looks_numeric(text: str) -> bool:
    return bool(re.search(r"[\d$%]", text))


def is_header_row(row: list[OcrItem]) -> bool:
    text = " ".join(item.text for item in row)
    return "Symbol" in text and "Last" in text and "Quantity" in text


def is_main_data_row(row: list[OcrItem]) -> bool:
    populated = 0
    for left, right in COLUMN_RANGES.values():
        if join_items(items_in_range(row, left, right)):
            populated += 1
    return populated >= 8


def is_range_row(row: list[OcrItem]) -> bool:
    if any(item.x >= 0.71 for item in row):
        return False
    range_items = [item for item in row if 0.57 <= item.x < 0.71]
    if len(range_items) < 3:
        return False
    return all(looks_numeric(item.text) for item in range_items)


def normalize_symbol_line(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"\s+[OeEG]$", "", text)
    text = re.sub(r"\s+(Call|Put)\s+[OeEG]$", r" \1", text)
    text = re.sub(r"\s*[\(\[\{]+$", "", text)
    text = re.sub(r"[^\w%$.)-]+$", "", text)
    return text.strip()


def normalize_description(text: str) -> str:
    return clean_text(text).replace("...", "").strip()


def normalize_percent_text(text: str, *, paired_amount: str = "") -> str:
    cleaned = clean_text(text).replace("/", "").replace(" ", "")
    if not cleaned:
        return ""
    if not cleaned.endswith("%"):
        return cleaned

    sign = ""
    if cleaned[0] in "+-":
        sign = cleaned[0]
        cleaned = cleaned[1:]
    elif paired_amount.startswith("-"):
        sign = "-"
    elif paired_amount.startswith("+"):
        sign = "+"

    number_text = cleaned[:-1].replace(",", ".")
    if number_text.count(".") > 1:
        head, *tail = number_text.split(".")
        number_text = head + "." + "".join(tail)

    if "." not in number_text:
        digits = re.sub(r"[^0-9]", "", number_text)
        if len(digits) >= 3:
            number_text = f"{digits[:-2]}.{digits[-2:]}"
        else:
            number_text = digits
    else:
        number_text = re.sub(r"[^0-9.]", "", number_text)

    if not number_text:
        return ""
    return f"{sign}{number_text}%"


def looks_like_expiration(text: str) -> bool:
    return bool(
        re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", clean_text(text))
    )


def parse_symbol_block(lines: list[str]) -> tuple[str, str, str, str]:
    cleaned = [clean_text(line) for line in lines if clean_text(line)]
    if not cleaned:
        return "", "unknown", "", ""

    first = normalize_symbol_line(cleaned[0])
    if " Call" in first or " Put" in first:
        expiration = ""
        for candidate in cleaned[1:]:
            if looks_like_expiration(candidate):
                expiration = clean_text(candidate)
                break
        return first, "option", "", expiration

    if len(cleaned) == 1:
        tokens = first.split()
        if len(tokens) >= 2 and re.fullmatch(r"[A-Z]{1,5}", tokens[0]):
            description_tokens = tokens[1:]
            if description_tokens and re.fullmatch(r"[A-Z]", description_tokens[0]):
                description_tokens = description_tokens[1:]
            return tokens[0], "equity", normalize_description(" ".join(description_tokens)), ""
        if (
            len(tokens) >= 2
            and re.fullmatch(r"[A-Z]{1,5}", tokens[-1])
            and tokens[-1] not in {"COM", "CORP", "INC", "FUND", "CL", "CLA"}
        ):
            return tokens[-1], "equity", normalize_description(" ".join(tokens[:-1])), ""

    description = ""
    for candidate in cleaned[1:]:
        if not looks_like_expiration(candidate):
            description = normalize_description(candidate)
            break
    return first, "equity", description, ""


def parse_main_row(row: list[OcrItem]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for name, (left, right) in COLUMN_RANGES.items():
        parsed[name] = join_items(items_in_range(row, left, right))

    total_gl = parsed.get("total_gl", "")
    percent_total_gl = parsed.get("percent_total_gl", "")
    if total_gl and not total_gl.startswith(("+", "-")) and percent_total_gl.startswith("-"):
        parsed["total_gl"] = f"-{total_gl.lstrip('-')}"
    if total_gl and not total_gl.startswith(("+", "-")) and percent_total_gl.startswith("+"):
        parsed["total_gl"] = f"+{total_gl.lstrip('+')}"

    parsed["percent_change"] = normalize_percent_text(parsed.get("percent_change", ""))
    parsed["percent_total_gl"] = normalize_percent_text(
        parsed.get("percent_total_gl", ""),
        paired_amount=parsed.get("total_gl", ""),
    )

    return parsed


def attach_range_row(record: dict[str, str], row: list[OcrItem]) -> None:
    for name in ("day_range_low", "day_range_high", "week_52_low", "week_52_high"):
        left, right = COLUMN_RANGES[name]
        record[name] = join_items(items_in_range(row, left, right))


def image_created_at(image_path: Path) -> datetime:
    stat_result = image_path.stat()
    timestamp = getattr(stat_result, "st_birthtime", stat_result.st_mtime)
    return datetime.fromtimestamp(timestamp).astimezone()


def csv_name(created_at: datetime) -> str:
    stamp = created_at.strftime("%Y%m%dT%H%M%S%z")
    return f"{OUTPUT_PREFIX}_{stamp}.csv"


def read_existing_image_file(csv_path: Path) -> str | None:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        first_row = next(reader, None)
    if not first_row:
        return None
    return first_row.get("image_file")


def row_pixel_bounds(row: list[OcrItem], image_height: int) -> tuple[int, int]:
    top = max(0, int(min((1 - (item.y + item.height)) * image_height for item in row)) - 4)
    bottom = min(image_height, int(max((1 - item.y) * image_height for item in row)) + 4)
    return top, bottom


def crop_ocr_items(
    image: Image.Image,
    row: list[OcrItem],
    left: float,
    right: float,
    scale: int = 10,
    threshold: int | None = None,
) -> list[OcrItem]:
    width, height = image.size
    top, bottom = row_pixel_bounds(row, height)
    crop_left = max(0, int(left * width) - 4)
    crop_right = min(width, int(right * width) + 4)
    crop = image.crop((crop_left, top, crop_right, bottom)).convert("L")
    if threshold is not None:
        crop = crop.point(lambda pixel: 255 if pixel > threshold else 0)
    crop = crop.resize((max(1, (crop_right - crop_left) * scale), max(1, (bottom - top) * scale)))

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        crop.save(temp_path)
        return run_vision_ocr(temp_path)
    finally:
        if temp_path.exists():
            os.unlink(temp_path)


def lines_from_crop_items(items: list[OcrItem]) -> list[str]:
    lines = []
    for grouped in group_rows(items, threshold=0.04):
        text = clean_text(" ".join(item.text for item in sorted(grouped, key=lambda entry: entry.x)))
        if text:
            lines.append(text)
    return lines


def extract_best_field_value(field_name: str, texts: list[str]) -> str:
    raw_texts = [text for text in texts if text and str(text).strip()]
    cleaned = [clean_text(text).replace("/", "") for text in raw_texts if clean_text(text)]
    if not cleaned:
        return ""

    if field_name == "quantity":
        candidates = []
        negative_hint = any("•" in text for text in raw_texts)
        for text in cleaned:
            candidates.extend(re.findall(r"[-+]?\d[\d,]*", text))
        candidates = [candidate for candidate in candidates if candidate not in {"+", "-"}]
        if not candidates:
            return ""
        best = max(candidates, key=lambda value: (value.startswith(("-", "+")), len(value)))
        if negative_hint and not best.startswith(("-", "+")):
            best = f"-{best}"
        return best

    if field_name == "volume":
        candidates = []
        for text in cleaned:
            candidates.extend(re.findall(r"\d[\d,]*", text))
        if not candidates:
            return ""
        return max(candidates, key=len)

    if field_name in {"last", "bid", "ask", "avg_cost", "total_gl", "change"}:
        candidates = []
        for text in cleaned:
            candidates.extend(re.findall(r"[+-]?\$?\d[\d,]*(?:\.\d+)?", text))
        if not candidates:
            return ""
        return max(candidates, key=lambda value: ("$" in value or value.startswith(("+", "-")), len(value)))

    if field_name in {"percent_change", "percent_total_gl"}:
        candidates = []
        for text in cleaned:
            candidates.extend(re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?%", text))
        if not candidates:
            return ""
        return max(candidates, key=len)

    candidates = []
    for text in cleaned:
        candidates.extend(re.findall(r"\d[\d,]*(?:\.\d+)?", text))
    if not candidates:
        return ""
    return max(candidates, key=lambda value: ("." in value, len(value)))


def repair_record_from_crop_texts(
    record: dict[str, str],
    left_lines: list[str],
    field_texts: dict[str, list[str]],
) -> dict[str, str]:
    repaired = dict(record)

    symbol, instrument_type, description, expiration = parse_symbol_block(left_lines)
    if symbol:
        repaired["symbol"] = symbol
    if instrument_type:
        repaired["instrument_type"] = instrument_type
    if description or repaired["instrument_type"] == "equity":
        repaired["description"] = description
    if expiration or repaired["instrument_type"] == "option":
        repaired["expiration"] = expiration

    if repaired.get("quantity"):
        normalized_quantity = extract_best_field_value("quantity", [repaired["quantity"]])
        if normalized_quantity:
            repaired["quantity"] = normalized_quantity

    for field_name, texts in field_texts.items():
        if repaired.get(field_name) and field_name != "quantity":
            continue
        candidate = extract_best_field_value(field_name, texts)
        if candidate:
            repaired[field_name] = candidate

    repaired["percent_change"] = normalize_percent_text(repaired.get("percent_change", ""))
    repaired["percent_total_gl"] = normalize_percent_text(
        repaired.get("percent_total_gl", ""),
        paired_amount=repaired.get("total_gl", ""),
    )

    return repaired


def repair_record_from_image_crop(
    image: Image.Image,
    row: list[OcrItem],
    record: dict[str, str],
) -> dict[str, str]:
    left_items = crop_ocr_items(image, row, 0.0, 0.20, scale=10)
    left_lines = lines_from_crop_items(left_items)

    field_texts: dict[str, list[str]] = {}
    for field_name, attempts in CELL_REPAIR_ATTEMPTS.items():
        if record.get(field_name) and field_name != "quantity":
            continue
        for left, right, scale, threshold in attempts:
            crop_items = crop_ocr_items(image, row, left, right, scale=scale, threshold=threshold)
            texts = [item.text for item in crop_items]
            if extract_best_field_value(field_name, texts):
                field_texts[field_name] = texts
                break

    if "quantity" not in field_texts:
        quantity_left, quantity_right = COLUMN_RANGES["quantity"]
        raw_quantity_texts = [item.text for item in items_in_range(row, quantity_left, quantity_right)]
        if extract_best_field_value("quantity", raw_quantity_texts):
            field_texts["quantity"] = raw_quantity_texts

    return repair_record_from_crop_texts(record, left_lines, field_texts)


def build_records(image_path: Path) -> list[dict[str, str]]:
    items = run_vision_ocr(image_path)
    rows = group_rows(items)
    created_at = image_created_at(image_path).isoformat(timespec="seconds")

    records: list[dict[str, str]] = []
    pending_symbol_lines: list[str] = []
    current_record: dict[str, str] | None = None

    with Image.open(image_path) as image:
        for row in rows:
            if is_header_row(row):
                continue

            left_lines = extract_symbol_lines(row)
            if is_main_data_row(row):
                if current_record is not None:
                    records.append(current_record)

                pending_symbol_lines.extend(left_lines)
                symbol, instrument_type, description, expiration = parse_symbol_block(
                    pending_symbol_lines
                )
                current_record = {
                    "schema_name": SCHEMA_NAME,
                    "image_file": image_path.name,
                    "created_at": created_at,
                    "symbol": symbol,
                    "instrument_type": instrument_type,
                    "description": description,
                    "expiration": expiration,
                    **parse_main_row(row),
                }
                current_record = repair_record_from_image_crop(
                    image=image,
                    row=row,
                    record=current_record,
                )
                pending_symbol_lines = []
                continue

            if is_range_row(row):
                if current_record is not None:
                    attach_range_row(current_record, row)
                continue

            pending_symbol_lines.extend(left_lines)

    if current_record is not None:
        records.append(current_record)

    return records


def write_csv(records: list[dict[str, str]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(records)


def discover_images() -> list[Path]:
    if not INPUT_DIR.exists():
        return []
    return sorted(path for path in INPUT_DIR.iterdir() if path.is_file() and path.suffix.lower() == ".png")


def process_image(image_path: Path) -> tuple[str, Path]:
    created_at = image_created_at(image_path)
    destination = OUTPUT_DIR / csv_name(created_at)
    if destination.exists():
        existing_image_file = read_existing_image_file(destination)
        if existing_image_file and existing_image_file != image_path.name:
            raise FileExistsError(
                "Output filename collision for timestamp "
                f"{created_at.isoformat(timespec='seconds')}: "
                f"{existing_image_file} and {image_path.name}"
            )
        return "skipped", destination

    records = build_records(image_path)
    write_csv(records, destination)
    return "extracted", destination


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Fidelity Trader+ monitoring screenshots into CSV files."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=INPUT_DIR,
        help="Directory containing source PNG files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory where CSV files will be written.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_dir = args.input_dir
    output_dir = args.output_dir

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    global OUTPUT_DIR
    OUTPUT_DIR = output_dir

    images = []
    if input_dir.exists():
        images = sorted(
            path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".png"
        )
    if not images:
        print(f"No PNG files found in {input_dir}.", file=sys.stderr)
        return 1

    extracted = 0
    skipped = 0
    for image_path in images:
        status, destination = process_image(image_path)
        if status == "extracted":
            extracted += 1
            print(f"extracted {image_path.name} -> {destination.name}")
        else:
            skipped += 1
            print(f"skipped   {image_path.name} -> {destination.name}")

    print(f"processed {len(images)} input file(s): {extracted} extracted, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
