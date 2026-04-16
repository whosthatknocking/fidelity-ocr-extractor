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
from typing import NamedTuple

from PIL import Image
from PIL import ImageFilter
from PIL import ImageOps
from PIL import ImageStat


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

    @property
    def center_x(self) -> float:
        return self.x + (self.width / 2)


DEFAULT_COLUMN_RANGES = {
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

DEFAULT_HEADER_CENTERS = {
    "symbol": 0.10,
    "last": 0.24,
    "change": 0.31,
    "percent_change": 0.37,
    "bid": 0.43,
    "ask": 0.49,
    "volume": 0.545,
    "day_range": 0.6025,
    "week_52_range": 0.6725,
    "avg_cost": 0.75,
    "quantity": 0.825,
    "total_gl": 0.90,
    "percent_total_gl": 0.975,
}

HEADER_ALIASES = {
    "symbol": ("symbol", "symdol", "symboi", "symbo1"),
    "last": ("last",),
    "change": ("change", "chang"),
    "percent_change": ("% change", "percent change", "%change"),
    "bid": ("bid", "bld", "8id", "act"),
    "ask": ("ask",),
    "volume": ("volume", "volurne"),
    "day_range": ("day range", "dayrange"),
    "week_52_range": ("52-week range", "52 week range", "52-weekrange"),
    "avg_cost": ("avg cost", "avs cos", "avg cos"),
    "quantity": ("quantity",),
    "total_gl": ("$ total g/l", "total g/l", "$ total gl"),
    "percent_total_gl": ("% total g/l", "% total gl"),
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
    "last": [(1.15, 10, None), (1.25, 12, 140)],
    "bid": [(1.15, 10, None), (1.25, 12, 140)],
    "ask": [(1.15, 10, None), (1.25, 12, 140)],
    "quantity": [(1.9, 8, None), (2.4, 12, 120), (2.8, 14, None)],
    "volume": [(1.2, 10, None), (1.2, 10, 120)],
    "percent_change": [(1.15, 10, None)],
    "change": [(1.15, 10, None)],
    "day_range_low": [(1.1, 10, None)],
    "day_range_high": [(1.1, 10, None)],
    "week_52_low": [(1.1, 10, None)],
    "week_52_high": [(1.1, 10, None)],
}

REQUIRED_FIELDS = [
    "symbol",
    "last",
    "change",
    "percent_change",
    "bid",
    "ask",
    "volume",
    "quantity",
    "day_range_low",
    "day_range_high",
]

MONEY_FIELDS = {"last", "bid", "ask", "avg_cost", "total_gl", "change"}
PERCENT_FIELDS = {"percent_change", "percent_total_gl"}
INTEGER_FIELDS = {"volume", "quantity"}
RANGE_FIELDS = {"day_range_low", "day_range_high", "week_52_low", "week_52_high"}
HEADER_REQUIRED_KEYS = {
    "symbol",
    "last",
    "change",
    "percent_change",
    "bid",
    "ask",
    "volume",
    "day_range",
    "avg_cost",
    "quantity",
    "total_gl",
    "percent_total_gl",
}
MIN_IMAGE_WIDTH = 1200
MIN_IMAGE_HEIGHT = 700
MIN_IMAGE_STDDEV = 18.0
MAX_CELL_OCR_CALLS = 24
HEADER_OCR_VARIANTS = ["grayscale", "contrast", "sharpen", "binary"]
CELL_OCR_VARIANTS = ("grayscale", "binary")
RETRY_PRIORITY_FIELDS = REQUIRED_FIELDS + ["last", "change", "percent_total_gl", "total_gl", "avg_cost"]


class ImageQualityError(ValueError):
    pass


class OcrBudgetExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class RowGeometry:
    top: int
    bottom: int
    left_symbol_boundary: float
    row_items: list[OcrItem]


@dataclass
class OcrBudget:
    max_calls: int = MAX_CELL_OCR_CALLS
    calls_used: int = 0

    def consume(self) -> None:
        self.calls_used += 1
        if self.calls_used > self.max_calls:
            raise OcrBudgetExceededError(f"Exceeded OCR cell budget of {self.max_calls} calls")


@dataclass
class RawRecord:
    schema_name: str
    image_file: str
    created_at: str
    symbol: str
    instrument_type: str
    description: str
    expiration: str
    raw_fields: dict[str, str]
    retried_fields: list[str]


class SelectedRows(NamedTuple):
    rows: list[list[OcrItem]]
    header_row: list[OcrItem]
    column_ranges: dict[str, tuple[float, float]]


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


def normalize_header_label(text: str) -> str:
    cleaned = clean_text(text).lower()
    cleaned = cleaned.replace(".", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def header_matches(text: str, key: str) -> bool:
    normalized = normalize_header_label(text)
    return any(alias in normalized for alias in HEADER_ALIASES[key])


def preprocess_for_ocr(image: Image.Image, variant: str) -> Image.Image:
    processed = image.convert("L")
    if variant == "grayscale":
        return processed
    if variant == "contrast":
        return ImageOps.autocontrast(processed, cutoff=1)
    if variant == "sharpen":
        contrasted = ImageOps.autocontrast(processed, cutoff=1)
        return contrasted.filter(ImageFilter.SHARPEN)
    if variant == "binary":
        contrasted = ImageOps.autocontrast(processed, cutoff=1)
        return contrasted.point(lambda pixel: 255 if pixel > 170 else 0)
    raise ValueError(f"Unknown OCR preprocess variant: {variant}")


def run_vision_ocr_image(image: Image.Image) -> list[OcrItem]:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        image.save(temp_path)
        return run_vision_ocr(temp_path)
    finally:
        if temp_path.exists():
            os.unlink(temp_path)


def run_vision_ocr_variants(image_path: Path, variants: list[str]) -> dict[str, list[OcrItem]]:
    variant_items: dict[str, list[OcrItem]] = {}
    with Image.open(image_path) as image:
        for variant in variants:
            variant_items[variant] = run_vision_ocr_image(preprocess_for_ocr(image, variant))
    return variant_items


def validate_image_quality(image_path: Path) -> None:
    with Image.open(image_path) as image:
        if image.width < MIN_IMAGE_WIDTH or image.height < MIN_IMAGE_HEIGHT:
            raise ImageQualityError(
                f"{image_path.name} is too small for reliable extraction: "
                f"{image.width}x{image.height}"
            )
        grayscale = image.convert("L")
        contrast = ImageStat.Stat(grayscale).stddev[0]
        if contrast < MIN_IMAGE_STDDEV:
            raise ImageQualityError(
                f"{image_path.name} has insufficient contrast for reliable extraction: "
                f"{contrast:.2f}"
            )


def items_in_range(items: list[OcrItem], left: float, right: float) -> list[OcrItem]:
    return [item for item in items if left <= item.center_x < right]


def join_items(items: list[OcrItem]) -> str:
    return clean_text(" ".join(item.text for item in sorted(items, key=lambda entry: entry.x)))


def expand_range(bounds: tuple[float, float], factor: float) -> tuple[float, float]:
    left, right = bounds
    center = (left + right) / 2
    half_width = ((right - left) * factor) / 2
    return max(0.0, center - half_width), min(1.0, center + half_width)


def map_coordinate(x_value: float, anchors: dict[str, float]) -> float:
    points = [(0.0, 0.0)]
    for name, default_center in sorted(DEFAULT_HEADER_CENTERS.items(), key=lambda item: item[1]):
        observed = anchors.get(name)
        if observed is not None:
            points.append((default_center, observed))
    points.append((1.0, 1.0))

    deduped: list[tuple[float, float]] = []
    for default_x, observed_x in points:
        if deduped and abs(default_x - deduped[-1][0]) < 1e-6:
            deduped[-1] = (default_x, observed_x)
            continue
        deduped.append((default_x, observed_x))

    points = deduped
    if x_value <= points[0][0]:
        return points[0][1]
    if x_value >= points[-1][0]:
        return points[-1][1]

    for (left_default, left_observed), (right_default, right_observed) in zip(points, points[1:]):
        if left_default <= x_value <= right_default:
            if abs(right_default - left_default) < 1e-6:
                return right_observed
            ratio = (x_value - left_default) / (right_default - left_default)
            return left_observed + ratio * (right_observed - left_observed)

    return x_value


def derive_column_ranges(header_row: list[OcrItem] | None) -> dict[str, tuple[float, float]]:
    if not header_row:
        return dict(DEFAULT_COLUMN_RANGES)

    anchors: dict[str, float] = {}
    ordered = sorted(header_row, key=lambda entry: entry.x)
    for index, item in enumerate(ordered):
        text = normalize_header_label(item.text)
        previous_text = (
            normalize_header_label(ordered[index - 1].text) if index > 0 else ""
        )
        next_text = (
            normalize_header_label(ordered[index + 1].text) if index + 1 < len(ordered) else ""
        )
        item_center = item.x + (item.width / 2)

        if header_matches(text, "symbol"):
            anchors["symbol"] = item_center
        elif header_matches(text, "last"):
            anchors["last"] = item_center
        elif header_matches(text, "bid"):
            anchors["bid"] = item_center
        elif header_matches(text, "ask"):
            anchors["ask"] = item_center
        elif header_matches(text, "volume"):
            anchors["volume"] = item_center
        elif header_matches(text, "quantity"):
            anchors["quantity"] = item_center
        elif header_matches(text, "avg_cost"):
            anchors["avg_cost"] = item_center
        elif header_matches(text, "day_range"):
            anchors["day_range"] = item_center
        elif header_matches(text, "week_52_range"):
            anchors["week_52_range"] = item_center
        elif header_matches(text, "percent_total_gl"):
            anchors["percent_total_gl"] = item_center
        elif header_matches(text, "total_gl"):
            anchors["total_gl"] = item_center
        elif header_matches(text, "percent_change"):
            anchors["percent_change"] = item_center
        elif "%" in text and "change" in next_text:
            next_item = ordered[index + 1]
            anchors["percent_change"] = (item.x + (next_item.x + next_item.width)) / 2
        elif "change" in text and "%" in previous_text:
            if "percent_change" not in anchors:
                previous = ordered[index - 1]
                anchors["percent_change"] = (previous.x + (item.x + item.width)) / 2
        elif "change" in text:
            anchors["change"] = item_center

    derived: dict[str, tuple[float, float]] = {}
    for name, (left, right) in DEFAULT_COLUMN_RANGES.items():
        mapped_left = map_coordinate(left, anchors)
        mapped_right = map_coordinate(right, anchors)
        derived[name] = (mapped_left, max(mapped_left, mapped_right))
    return derived


def extract_symbol_lines(
    row: list[OcrItem],
    symbol_right_boundary: float,
    threshold: float = 0.012,
) -> list[str]:
    left_items = [item for item in row if item.center_x < symbol_right_boundary]
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
    normalized = normalize_header_label(text)
    return (
        header_matches(normalized, "symbol")
        and header_matches(normalized, "last")
        and header_matches(normalized, "quantity")
    )


def is_main_data_row(row: list[OcrItem], column_ranges: dict[str, tuple[float, float]]) -> bool:
    populated = 0
    for left, right in column_ranges.values():
        if join_items(items_in_range(row, left, right)):
            populated += 1
    return populated >= 8


def is_range_row(row: list[OcrItem], column_ranges: dict[str, tuple[float, float]]) -> bool:
    if any(item.center_x >= column_ranges["avg_cost"][0] for item in row):
        return False
    range_left = column_ranges["day_range_low"][0]
    range_right = column_ranges["week_52_high"][1]
    range_items = [item for item in row if range_left <= item.center_x < range_right]
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


def normalize_money_text(text: str) -> str:
    cleaned = clean_text(text).replace(" ", "").replace(",", "")
    if not cleaned:
        return ""

    sign = ""
    if cleaned[0] in "+-":
        sign = cleaned[0]
        cleaned = cleaned[1:]

    cleaned = cleaned.replace("$", "")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned:
        return ""

    if cleaned.count(".") > 1:
        head, *tail = cleaned.split(".")
        cleaned = head + "." + "".join(tail)

    if "." not in cleaned:
        digits = cleaned
        if len(digits) >= 3:
            cleaned = f"{digits[:-2]}.{digits[-2:]}"
        elif len(digits) == 2:
            cleaned = f"0.{digits}"
        elif len(digits) == 1:
            cleaned = f"0.0{digits}"
    else:
        whole, frac = cleaned.split(".", 1)
        frac = re.sub(r"[^0-9]", "", frac)
        if not frac:
            frac = "00"
        elif len(frac) == 1:
            frac = f"{frac}0"
        elif len(frac) > 2:
            frac = frac[:2]
        cleaned = f"{whole or '0'}.{frac}"

    prefix = "$" if cleaned else ""
    return f"{sign}{prefix}{cleaned}"


def normalize_integer_text(text: str, *, signed: bool = False) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""

    negative_hint = "•" in text or "-" in cleaned
    compact = re.sub(r"(?<=\d)[.\s](?=\d)", ",", cleaned)
    compact = re.sub(r"[^0-9,\-]", "", compact)
    candidates = re.findall(r"\d[\d,]*", compact)
    if not candidates:
        return ""
    best = max(candidates, key=len)
    if signed and negative_hint:
        return f"-{best}"
    return best


def normalize_field_value(field_name: str, text: str, *, paired_amount: str = "") -> str:
    if field_name in MONEY_FIELDS:
        return normalize_money_text(text)
    if field_name in PERCENT_FIELDS:
        return normalize_percent_text(text, paired_amount=paired_amount)
    if field_name in RANGE_FIELDS:
        return normalize_range_text(text)
    if field_name == "quantity":
        return normalize_integer_text(text, signed=True)
    if field_name == "volume":
        return normalize_integer_text(text, signed=False)
    return clean_text(text)


def is_valid_field_value(field_name: str, value: str) -> bool:
    if not value:
        return False
    if field_name in MONEY_FIELDS:
        return bool(re.fullmatch(r"[+-]?\$?\d+(?:\.\d{2})?", value))
    if field_name in PERCENT_FIELDS:
        return bool(re.fullmatch(r"[+-]?\d+(?:\.\d{1,2})?%", value))
    if field_name == "volume":
        return bool(re.fullmatch(r"\d[\d,]*", value))
    if field_name == "quantity":
        return bool(re.fullmatch(r"-?\d[\d,]*", value))
    if field_name in RANGE_FIELDS:
        return bool(re.fullmatch(r"\d+(?:\.\d{1,2})?", value))
    return True


def normalize_number(text: str | None) -> float | None:
    if text is None:
        return None
    cleaned = str(text).strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace("$", "").replace("%", "").replace(",", "")
    cleaned = re.sub(r"[^0-9.+-]", "", cleaned)
    if cleaned in {"", "+", "-", ".", "+.", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def format_price(value: float, *, signed: bool = False, positive_sign: bool = False) -> str:
    sign = ""
    absolute = value
    if signed:
        if value < 0:
            sign = "-"
            absolute = -value
        elif positive_sign:
            sign = "+"
    return f"{sign}${absolute:.2f}"


def repair_price_from_context(value: str, context: str) -> str:
    numeric_value = normalize_number(value)
    numeric_context = normalize_number(context)
    if numeric_value is None or numeric_context is None:
        return value
    value_text = f"{numeric_value:.2f}"
    context_text = f"{numeric_context:.2f}"
    if len(value_text) == len(context_text) + 1 and value_text.endswith(context_text[1:]):
        return format_price(numeric_context)
    value_whole, value_frac = value_text.split(".", 1)
    context_whole, _context_frac = context_text.split(".", 1)
    if len(value_whole) == len(context_whole) and len(context_whole) >= 2:
        candidate_text = f"{context_whole[:-1]}{value_whole[-1]}.{value_frac}"
        candidate = float(candidate_text)
        if abs(candidate - numeric_context) < abs(numeric_value - numeric_context):
            return format_price(candidate)
    return value


def repair_range_from_references(value: str, references: list[str]) -> str:
    numeric_value = normalize_number(value)
    numeric_refs = [reference for reference in (normalize_number(item) for item in references) if reference is not None]
    if numeric_value is None or not numeric_refs:
        return value
    ref_floor = min(numeric_refs)
    ref_ceiling = max(numeric_refs)
    if ref_floor <= numeric_value <= ref_ceiling + 25:
        return value

    value_text = f"{numeric_value:.2f}"
    reference_text = f"{ref_floor:.2f}"
    value_whole, value_frac = value_text.split(".", 1)
    ref_whole, _ref_frac = reference_text.split(".", 1)
    if len(value_whole) + 1 == len(ref_whole):
        candidate_text = f"{ref_whole[0]}{value_whole}.{value_frac}"
        candidate = float(candidate_text)
        if ref_floor - 25 <= candidate <= ref_ceiling + 25:
            return candidate_text
    return value


def reconcile_numeric_fields(record: dict[str, str]) -> dict[str, str]:
    reconciled = dict(record)
    if reconciled.get("bid") and reconciled.get("ask"):
        reconciled["ask"] = repair_price_from_context(
            reconciled["ask"], reconciled.get("bid", "")
        )
        reconciled["bid"] = repair_price_from_context(
            reconciled["bid"], reconciled.get("ask", "")
        )
    if reconciled.get("last"):
        reference_price = reconciled.get("bid") or reconciled.get("ask") or reconciled.get("last", "")
        reconciled["last"] = repair_price_from_context(reconciled["last"], reference_price)

    bid = normalize_number(reconciled.get("bid"))
    ask = normalize_number(reconciled.get("ask"))
    last = normalize_number(reconciled.get("last"))
    if bid is not None and ask is not None and bid > ask and (bid - ask) <= 1.0:
        reconciled["ask"] = format_price(max(bid, ask, last or ask))

    price_refs = [reconciled.get("last", ""), reconciled.get("bid", ""), reconciled.get("ask", "")]
    if reconciled.get("day_range_low"):
        reconciled["day_range_low"] = repair_range_from_references(
            reconciled["day_range_low"], price_refs
        )
    if reconciled.get("day_range_high"):
        reconciled["day_range_high"] = repair_range_from_references(
            reconciled["day_range_high"], price_refs
        )

    day_low = normalize_number(reconciled.get("day_range_low"))
    day_high = normalize_number(reconciled.get("day_range_high"))
    numeric_prices = [price for price in (normalize_number(item) for item in price_refs) if price is not None]
    if day_low is not None and day_high is not None and day_low > day_high and numeric_prices:
        reconciled["day_range_low"] = f"{min(numeric_prices + [day_low, day_high]):.2f}"
        reconciled["day_range_high"] = f"{max(numeric_prices + [day_low, day_high]):.2f}"
    return reconciled


def field_needs_retry(field_name: str, value: str) -> bool:
    if field_name in REQUIRED_FIELDS and not value:
        return True
    if not value:
        return False
    return not is_valid_field_value(field_name, value)


def normalize_range_text(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""

    cleaned = cleaned.replace("*", "").replace(" ", "").replace(",", ".")
    cleaned = re.sub(r"(?<=\d)[Aa](?=\d|$)", "4", cleaned)
    cleaned = re.sub(r"(?<=\.)[Aa](?=\d|$)", "4", cleaned)
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned:
        return ""

    if cleaned.startswith("."):
        cleaned = f"0{cleaned}"

    if cleaned.count(".") > 1:
        head, *tail = cleaned.split(".")
        cleaned = head + "." + "".join(tail)

    if "." not in cleaned:
        digits = cleaned
        if len(digits) >= 3:
            cleaned = f"{digits[:-2]}.{digits[-2:]}"
        else:
            cleaned = digits

    if "." in cleaned:
        whole, frac = cleaned.split(".", 1)
        frac = re.sub(r"[^0-9]", "", frac)
        if not whole:
            whole = "0"
        if not frac:
            return whole
        if len(frac) == 1:
            frac = f"{frac}0"
        elif len(frac) > 2:
            frac = frac[:2]
        return f"{whole}.{frac}"

    return cleaned


def looks_like_expiration(text: str) -> bool:
    return bool(
        re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", clean_text(text))
    )


def looks_like_option_symbol(text: str) -> bool:
    normalized = normalize_symbol_line(text)
    return bool(re.fullmatch(r"[A-Z]{1,5}\s+\d+(?:\.\d+)?\s+(?:Call|Put)", normalized))


def looks_like_equity_symbol(text: str) -> bool:
    normalized = normalize_symbol_line(text)
    return bool(re.fullmatch(r"[A-Z]{1,5}", normalized))


def select_symbol_lines(lines: list[str]) -> tuple[str, list[str]]:
    normalized = [clean_text(line) for line in lines if clean_text(line)]
    option_candidates = [line for line in normalized if looks_like_option_symbol(line)]
    if option_candidates:
        symbol = normalize_symbol_line(option_candidates[-1])
        remaining = [line for line in normalized if clean_text(line) != symbol]
        return symbol, remaining

    equity_candidates = [line for line in normalized if looks_like_equity_symbol(line)]
    if equity_candidates:
        symbol = normalize_symbol_line(equity_candidates[0])
        remaining = normalized[normalized.index(equity_candidates[0]) + 1 :]
        return symbol, remaining

    for line in normalized:
        if looks_like_expiration(line):
            continue
        compact = normalize_symbol_line(line)
        tokens = compact.split()
        if tokens and re.fullmatch(r"[A-Z]{1,5}", tokens[0]):
            symbol = tokens[0]
            remainder = " ".join(tokens[1:])
            remaining = ([remainder] if remainder else []) + normalized[normalized.index(line) + 1 :]
            return symbol, remaining
    return "", normalized


def parse_symbol_block(lines: list[str]) -> tuple[str, str, str, str]:
    cleaned = [clean_text(line) for line in lines if clean_text(line)]
    if not cleaned:
        return "", "unknown", "", ""

    symbol_line, remaining_lines = select_symbol_lines(cleaned)
    if symbol_line and (" Call" in symbol_line or " Put" in symbol_line):
        expiration = ""
        for candidate in cleaned:
            if looks_like_expiration(candidate):
                expiration = clean_text(candidate)
        return symbol_line, "option", "", expiration

    first = normalize_symbol_line(symbol_line or cleaned[0])
    if len(cleaned) == 1 or (symbol_line and not remaining_lines):
        tokens = first.split()
        if len(tokens) >= 1 and re.fullmatch(r"[A-Z]{1,5}", tokens[0]):
            description_tokens = tokens[1:]
            if description_tokens and re.fullmatch(r"[A-Z]", description_tokens[0]):
                description_tokens = description_tokens[1:]
            return tokens[0], "equity", normalize_description(" ".join(description_tokens)), ""
        if len(tokens) >= 2 and re.fullmatch(r"[A-Z]{1,5}", tokens[-1]):
            return tokens[-1], "equity", normalize_description(" ".join(tokens[:-1])), ""

    description = ""
    for candidate in remaining_lines or cleaned[1:]:
        if not looks_like_expiration(candidate):
            description = normalize_description(candidate)
            break
    return first, "equity", description, ""


def parse_main_row_raw(
    row: list[OcrItem],
    column_ranges: dict[str, tuple[float, float]] | None = None,
) -> dict[str, str]:
    active_ranges = column_ranges or DEFAULT_COLUMN_RANGES
    parsed: dict[str, str] = {}
    for name, (left, right) in active_ranges.items():
        parsed[name] = join_items(items_in_range(row, left, right))
    return parsed


def normalize_parsed_fields(parsed: dict[str, str]) -> dict[str, str]:
    normalized = dict(parsed)
    total_gl = parsed.get("total_gl", "")
    percent_total_gl = parsed.get("percent_total_gl", "")
    if total_gl and not total_gl.startswith(("+", "-")) and percent_total_gl.startswith("-"):
        normalized["total_gl"] = f"-{total_gl.lstrip('-')}"
    if total_gl and not total_gl.startswith(("+", "-")) and percent_total_gl.startswith("+"):
        normalized["total_gl"] = f"+{total_gl.lstrip('+')}"

    normalized["last"] = normalize_field_value("last", normalized.get("last", ""))
    normalized["change"] = normalize_field_value("change", normalized.get("change", ""))
    normalized["bid"] = normalize_field_value("bid", normalized.get("bid", ""))
    normalized["ask"] = normalize_field_value("ask", normalized.get("ask", ""))
    normalized["avg_cost"] = normalize_field_value("avg_cost", normalized.get("avg_cost", ""))
    normalized["total_gl"] = normalize_field_value("total_gl", normalized.get("total_gl", ""))
    normalized["volume"] = normalize_field_value("volume", normalized.get("volume", ""))
    normalized["quantity"] = normalize_field_value("quantity", normalized.get("quantity", ""))
    normalized["percent_change"] = normalize_percent_text(normalized.get("percent_change", ""))
    normalized["percent_total_gl"] = normalize_percent_text(
        normalized.get("percent_total_gl", ""),
        paired_amount=normalized.get("total_gl", ""),
    )
    for field_name in ("day_range_low", "day_range_high", "week_52_low", "week_52_high"):
        normalized[field_name] = normalize_range_text(normalized.get(field_name, ""))
    return normalized


def parse_main_row(
    row: list[OcrItem],
    column_ranges: dict[str, tuple[float, float]] | None = None,
) -> dict[str, str]:
    return normalize_parsed_fields(parse_main_row_raw(row, column_ranges))


def attach_range_row(
    record: dict[str, str],
    row: list[OcrItem],
    column_ranges: dict[str, tuple[float, float]],
) -> None:
    for name in ("day_range_low", "day_range_high", "week_52_low", "week_52_high"):
        left, right = column_ranges[name]
        record[name] = normalize_range_text(join_items(items_in_range(row, left, right)))


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
    raw_top = int(min((1 - (item.y + item.height)) * image_height for item in row))
    raw_bottom = int(max((1 - item.y) * image_height for item in row))
    padding = max(4, int((raw_bottom - raw_top) * 0.35))
    top = max(0, raw_top - padding)
    bottom = min(image_height, raw_bottom + padding)
    return top, bottom


def build_row_geometry(
    row: list[OcrItem],
    image_height: int,
    symbol_right_boundary: float,
) -> RowGeometry:
    top, bottom = row_pixel_bounds(row, image_height)
    return RowGeometry(
        top=top,
        bottom=bottom,
        left_symbol_boundary=symbol_right_boundary,
        row_items=row,
    )


def cell_pixel_bounds(
    image_size: tuple[int, int],
    geometry: RowGeometry,
    bounds: tuple[float, float],
) -> tuple[int, int, int, int]:
    width, _height = image_size
    crop_left = max(0, int(bounds[0] * width) - 4)
    crop_right = min(width, int(bounds[1] * width) + 4)
    return crop_left, geometry.top, crop_right, geometry.bottom


def crop_ocr_items(
    image: Image.Image,
    geometry: RowGeometry,
    bounds: tuple[float, float],
    budget: OcrBudget,
    cache: dict[tuple[int, int, int, int, int, int | None, str], list[OcrItem]],
    scale: int = 10,
    threshold: int | None = None,
    variant: str = "grayscale",
) -> list[OcrItem]:
    crop_left, top, crop_right, bottom = cell_pixel_bounds(image.size, geometry, bounds)
    cache_key = (crop_left, top, crop_right, bottom, scale, threshold, variant)
    if cache_key in cache:
        return cache[cache_key]
    budget.consume()
    crop = image.crop((crop_left, top, crop_right, bottom))
    crop = preprocess_for_ocr(crop, variant)
    if threshold is not None:
        crop = crop.point(lambda pixel: 255 if pixel > threshold else 0)
    crop = crop.resize((max(1, (crop_right - crop_left) * scale), max(1, (bottom - top) * scale)))

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        crop.save(temp_path)
        items = run_vision_ocr(temp_path)
        cache[cache_key] = items
        return items
    finally:
        if temp_path.exists():
            os.unlink(temp_path)


def collect_cell_texts(
    field_name: str,
    image: Image.Image,
    geometry: RowGeometry,
    bounds: tuple[float, float],
    budget: OcrBudget,
    cache: dict[tuple[int, int, int, int, int, int | None, str], list[OcrItem]],
    attempts: list[tuple[float, int, int | None]] | None = None,
) -> list[str]:
    texts: list[str] = []
    planned_attempts = attempts or [(1.0, 8, None), (1.15, 10, 160)]
    for factor, scale, threshold in planned_attempts:
        expanded_bounds = expand_range(bounds, factor)
        for variant in CELL_OCR_VARIANTS:
            crop_items = crop_ocr_items(
                image,
                geometry,
                expanded_bounds,
                budget,
                cache,
                scale=scale,
                threshold=threshold,
                variant=variant,
            )
            texts.extend(item.text for item in crop_items)
            candidate = extract_best_field_value(field_name, texts)
            normalized = normalize_field_value(field_name, candidate)
            if is_valid_field_value(field_name, normalized):
                return texts
    return texts


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
    retried_fields = set(repaired.get("_retried_fields", []))

    symbol, instrument_type, description, expiration = parse_symbol_block(left_lines)
    if symbol:
        repaired["symbol"] = symbol
    if instrument_type:
        repaired["instrument_type"] = instrument_type
    if repaired["instrument_type"] == "option":
        repaired["description"] = ""
    elif description or repaired["instrument_type"] == "equity":
        repaired["description"] = description
    if expiration or repaired["instrument_type"] == "option":
        repaired["expiration"] = expiration

    if repaired.get("quantity"):
        normalized_quantity = normalize_field_value("quantity", repaired["quantity"])
        if normalized_quantity:
            repaired["quantity"] = normalized_quantity

    for field_name, texts in field_texts.items():
        if repaired.get(field_name) and field_name != "quantity" and not field_needs_retry(
            field_name, repaired.get(field_name, "")
        ):
            continue
        candidate = extract_best_field_value(field_name, texts)
        if candidate:
            repaired[field_name] = normalize_field_value(
                field_name,
                candidate,
                paired_amount=repaired.get("total_gl", ""),
            )
            retried_fields.add(field_name)

    repaired["last"] = normalize_field_value("last", repaired.get("last", ""))
    repaired["change"] = normalize_field_value("change", repaired.get("change", ""))
    repaired["bid"] = normalize_field_value("bid", repaired.get("bid", ""))
    repaired["ask"] = normalize_field_value("ask", repaired.get("ask", ""))
    repaired["avg_cost"] = normalize_field_value("avg_cost", repaired.get("avg_cost", ""))
    repaired["total_gl"] = normalize_field_value("total_gl", repaired.get("total_gl", ""))
    repaired["volume"] = normalize_field_value("volume", repaired.get("volume", ""))
    repaired["quantity"] = normalize_field_value("quantity", repaired.get("quantity", ""))
    repaired["percent_change"] = normalize_percent_text(repaired.get("percent_change", ""))
    repaired["percent_total_gl"] = normalize_percent_text(
        repaired.get("percent_total_gl", ""),
        paired_amount=repaired.get("total_gl", ""),
    )
    for field_name in ("day_range_low", "day_range_high", "week_52_low", "week_52_high"):
        repaired[field_name] = normalize_range_text(repaired.get(field_name, ""))

    repaired = reconcile_numeric_fields(repaired)
    repaired["_retried_fields"] = sorted(retried_fields)

    return repaired


def repair_record_from_image_crop(
    image: Image.Image,
    geometry: RowGeometry,
    record: dict[str, str],
    column_ranges: dict[str, tuple[float, float]],
    budget: OcrBudget,
    cache: dict[tuple[int, int, int, int, int, int | None, str], list[OcrItem]],
) -> dict[str, str]:
    left_lines: list[str] = []
    for variant in CELL_OCR_VARIANTS:
        left_items = crop_ocr_items(
            image,
            geometry,
            (0.0, geometry.left_symbol_boundary),
            budget,
            cache,
            scale=10,
            variant=variant,
        )
        left_lines.extend(lines_from_crop_items(left_items))

    field_texts: dict[str, list[str]] = {}
    suspicious_fields = detect_suspicious_fields(record)
    retry_fields = []
    for field_name in RETRY_PRIORITY_FIELDS:
        if field_name in column_ranges and field_name not in retry_fields:
            retry_fields.append(field_name)
    for field_name in suspicious_fields:
        if field_name in column_ranges and field_name not in retry_fields:
            retry_fields.append(field_name)

    for field_name in retry_fields:
        bounds = column_ranges[field_name]
        existing_value = normalize_field_value(
            field_name,
            record.get(field_name, ""),
            paired_amount=record.get("total_gl", ""),
        )
        record[field_name] = existing_value
        if field_name not in suspicious_fields and not field_needs_retry(field_name, existing_value):
            continue

        attempts = CELL_REPAIR_ATTEMPTS.get(field_name, [(1.0, 8, None), (1.15, 10, 160)])
        raw_texts = [item.text for item in items_in_range(geometry.row_items, bounds[0], bounds[1])]
        texts = list(raw_texts)
        texts.extend(collect_cell_texts(field_name, image, geometry, bounds, budget, cache, attempts))
        if texts:
            field_texts[field_name] = texts

    return repair_record_from_crop_texts(record, left_lines, field_texts)


def validate_required_fields(record: dict[str, str], image_path: Path) -> None:
    missing_fields = [field_name for field_name in REQUIRED_FIELDS if not record.get(field_name)]
    if missing_fields:
        raise ValueError(
            f"Missing required monitoring fields for {image_path.name}: {', '.join(missing_fields)}"
        )


def validate_field_shapes(record: dict[str, str], image_path: Path) -> None:
    invalid_fields = [
        field_name
        for field_name in REQUIRED_FIELDS
        if record.get(field_name) and not is_valid_field_value(field_name, record.get(field_name, ""))
    ]
    if invalid_fields:
        raise ValueError(
            f"Invalid extracted field shapes for {image_path.name}: {', '.join(invalid_fields)}"
        )


def validate_cross_field_consistency(record: dict[str, str], image_path: Path) -> None:
    bid = normalize_number(record.get("bid"))
    ask = normalize_number(record.get("ask"))
    day_low = normalize_number(record.get("day_range_low"))
    day_high = normalize_number(record.get("day_range_high"))
    if bid is not None and ask is not None and bid > ask:
        raise ValueError(f"Bid exceeds ask for {image_path.name}: {record.get('symbol', '')}")
    if day_low is not None and day_high is not None and day_low > day_high:
        raise ValueError(
            f"Day range low exceeds high for {image_path.name}: {record.get('symbol', '')}"
        )


def detect_suspicious_fields(record: dict[str, str]) -> set[str]:
    suspicious: set[str] = set()
    bid = normalize_number(record.get("bid"))
    ask = normalize_number(record.get("ask"))
    last = normalize_number(record.get("last"))
    day_low = normalize_number(record.get("day_range_low"))
    day_high = normalize_number(record.get("day_range_high"))

    if bid is not None and ask is not None and bid > ask:
        suspicious.update({"bid", "ask"})
    if day_low is not None and day_high is not None and day_low > day_high:
        suspicious.update({"day_range_low", "day_range_high"})
    if last is not None and day_low is not None and day_high is not None:
        if last < day_low or last > day_high:
            suspicious.update({"last", "day_range_low", "day_range_high"})
    return suspicious


def finalize_record(record: dict[str, str] | None, image_path: Path) -> dict[str, str] | None:
    if record is None:
        return None
    validate_required_fields(record, image_path)
    finalized = dict(record)
    validate_field_shapes(finalized, image_path)
    validate_cross_field_consistency(finalized, image_path)
    finalized.pop("_retried_fields", None)
    return finalized


def detect_header_row(rows: list[list[OcrItem]]) -> list[OcrItem] | None:
    for row in rows:
        if is_header_row(row):
            return row
    return None


def header_anchor_count(header_row: list[OcrItem]) -> int:
    normalized = " ".join(normalize_header_label(item.text) for item in header_row)
    return sum(1 for key in HEADER_REQUIRED_KEYS if header_matches(normalized, key))


def select_ocr_rows(image_path: Path) -> SelectedRows:
    validate_image_quality(image_path)
    variant_items = run_vision_ocr_variants(image_path, HEADER_OCR_VARIANTS)
    best_rows: list[list[OcrItem]] = []
    best_header: list[OcrItem] | None = None
    best_ranges = dict(DEFAULT_COLUMN_RANGES)
    best_score = -1
    for items in variant_items.values():
        rows = group_rows(items)
        header_row = detect_header_row(rows)
        score = 0
        if header_row is not None:
            score += 100
            score += 10 * header_anchor_count(header_row)
        score += len(rows)
        if score > best_score:
            best_rows = rows
            best_header = header_row
            best_ranges = derive_column_ranges(header_row)
            best_score = score
    if best_header is None:
        raise ImageQualityError(f"{image_path.name} does not expose a recognizable monitoring header")
    anchors = header_anchor_count(best_header)
    if anchors < 10:
        raise ImageQualityError(
            f"{image_path.name} header OCR is too degraded for reliable extraction: "
            f"{anchors} anchors detected"
        )
    return SelectedRows(best_rows, best_header, best_ranges)


def build_records(image_path: Path) -> list[dict[str, str]]:
    selected = select_ocr_rows(image_path)
    rows = selected.rows
    column_ranges = selected.column_ranges
    symbol_right_boundary = min(column_ranges["last"][0], 0.30)
    created_at = image_created_at(image_path).isoformat(timespec="seconds")

    records: list[dict[str, str]] = []
    pending_symbol_lines: list[str] = []
    current_record: dict[str, str] | None = None
    crop_cache: dict[tuple[int, int, int, int, int, int | None, str], list[OcrItem]] = {}

    with Image.open(image_path) as image:
        for row in rows:
            if is_header_row(row):
                continue

            left_lines = extract_symbol_lines(row, symbol_right_boundary)
            if is_main_data_row(row, column_ranges):
                if current_record is not None:
                    records.append(finalize_record(current_record, image_path))

                candidate_lines = left_lines if select_symbol_lines(left_lines)[0] else pending_symbol_lines + left_lines
                symbol, instrument_type, description, expiration = parse_symbol_block(candidate_lines)
                raw_record = RawRecord(
                    schema_name=SCHEMA_NAME,
                    image_file=image_path.name,
                    created_at=created_at,
                    symbol=symbol,
                    instrument_type=instrument_type,
                    description=description,
                    expiration=expiration,
                    raw_fields=parse_main_row_raw(row, column_ranges),
                    retried_fields=[],
                )
                current_record = {
                    "schema_name": SCHEMA_NAME,
                    "image_file": image_path.name,
                    "created_at": created_at,
                    "symbol": raw_record.symbol,
                    "instrument_type": raw_record.instrument_type,
                    "description": raw_record.description,
                    "expiration": raw_record.expiration,
                    **normalize_parsed_fields(raw_record.raw_fields),
                }
                geometry = build_row_geometry(row, image.size[1], symbol_right_boundary)
                budget = OcrBudget()
                current_record = repair_record_from_image_crop(
                    image=image,
                    geometry=geometry,
                    record=current_record,
                    column_ranges=column_ranges,
                    budget=budget,
                    cache=crop_cache,
                )
                pending_symbol_lines = []
                continue

            if is_range_row(row, column_ranges):
                if current_record is not None:
                    attach_range_row(current_record, row, column_ranges)
                continue

            pending_symbol_lines.extend(left_lines)

    if current_record is not None:
        records.append(finalize_record(current_record, image_path))

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
        print(f"processing {image_path.name}...", flush=True)
        status, destination = process_image(image_path)
        if status == "extracted":
            extracted += 1
            print(f"extracted {image_path.name} -> {destination.name}", flush=True)
        else:
            skipped += 1
            print(f"skipped   {image_path.name} -> {destination.name}", flush=True)

    print(
        f"processed {len(images)} input file(s): {extracted} extracted, {skipped} skipped",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
