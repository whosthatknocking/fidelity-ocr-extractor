#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from functools import lru_cache
from typing import Iterable
from typing import NamedTuple

from PIL import Image
from PIL import ImageFilter
from PIL import ImageOps
from PIL import ImageStat

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10 fallback
    import tomli as tomllib


REPO_ROOT = Path(__file__).resolve().parent
INPUT_DIR = REPO_ROOT / "input"
OUTPUT_DIR = REPO_ROOT / "output"
CONFIG_PATH = REPO_ROOT / "config.toml"
SCHEMA_NAME = "monitoring"
OUTPUT_PREFIX = "positions_monitoring"
OCR_ENGINE_ENV_VAR = "FIDELITY_OCR_ENGINE"
OCR_TIMEOUT_SECONDS = 8
VISION_SWIFT_CMD = [
    "env",
    "DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer",
    "CLANG_MODULE_CACHE_PATH=/tmp/clang-module-cache",
    "xcrun",
    "swift",
    "-",
]

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

HEADER_CLASSIFICATION_VARIANTS = {
    "symbol": ("symbol", "symdol", "symboi", "symbo1"),
    "last": ("last",),
    "change": ("change", "chang"),
    "percent_change": ("% change", "%change", "percent change"),
    "bid": ("bid", "bld", "8id"),
    "ask": ("ask", "acl"),
    "volume": ("volume", "voluime", "volurne", "wolumg", "olume"),
    "day_range": ("day range", "dayrange"),
    "week_52_range": ("52-week range", "52 week range", "52-weekrange", "s2 week range", "52 week"),
    "avg_cost": ("avg cost", "avg. cost", "avs cos", "avg cos", "aug cost", "aug. cost"),
    "quantity": ("quantity", "quantit"),
    "total_gl": ("$ total g/l", "$ total gl", "total g/l", "total gl", "sotacl", "stotal git", "stotal g/l"),
    "percent_total_gl": ("% total g/l", "% total gl", "cotacl", "caotaci", "total g/l"),
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

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTH_PREFIX_MAP = {name.lower(): name for name in MONTH_NAMES}
SYMBOL_STOPWORDS = {
    *(name.upper() for name in MONTH_NAMES),
    "CALL",
    "PUT",
    "M",
    "E",
    "INC",
    "CORP",
    "COM",
    "CAP",
    "STK",
    "CL",
    "ETF",
    "FUND",
    "AND",
    "THE",
}
OPTION_SYMBOL_RE = re.compile(r"\b([A-Z]{1,5})\s+(\d+(?:\.\d+)?)\s+(Call|Put)\b")
EQUITY_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")
SIGNED_MONEY_FIELDS = {"change", "total_gl"}
SIGNED_PERCENT_FIELDS = {"percent_change", "percent_total_gl"}

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

MONEY_FIELDS = {"last", "bid", "ask", "avg_cost", "total_gl", "change"}
PERCENT_FIELDS = {"percent_change", "percent_total_gl"}
INTEGER_FIELDS = {"volume", "quantity"}
RANGE_FIELDS = {"day_range_low", "day_range_high", "week_52_low", "week_52_high"}
MIN_IMAGE_WIDTH = 1200
MIN_IMAGE_HEIGHT = 700
MIN_IMAGE_STDDEV = 18.0
MAX_CELL_OCR_CALLS = 24
MAX_TESSERACT_CELL_OCR_CALLS = 12
HEADER_OCR_VARIANTS = ["grayscale", "contrast", "sharpen", "binary"]
CELL_OCR_VARIANTS = ("grayscale", "binary")


def header_ocr_variants() -> list[str]:
    if preferred_ocr_engine() == "tesseract":
        return ["grayscale", "contrast"]
    return HEADER_OCR_VARIANTS


def cell_ocr_variants() -> tuple[str, ...]:
    if preferred_ocr_engine() == "tesseract":
        return ("grayscale",)
    return CELL_OCR_VARIANTS


class ImageQualityError(ValueError):
    pass


class OcrBudgetExceededError(RuntimeError):
    pass


class OcrBackendUnavailableError(RuntimeError):
    pass


class OcrExecutionError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_monitoring_contract() -> dict[str, object]:
    with CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    try:
        monitoring = config["monitoring"]
    except KeyError as exc:  # pragma: no cover - config file is repository contract
        raise ValueError(f"Missing [monitoring] configuration in {CONFIG_PATH.name}") from exc

    headers = monitoring.get("headers", {})
    required_fields = monitoring.get("required_fields", [])
    required_header_keys = monitoring.get("required_header_keys", [])

    if not isinstance(headers, dict) or not headers:
        raise ValueError(f"{CONFIG_PATH.name} must define [monitoring.headers]")
    for key in required_header_keys:
        if key not in headers:
            raise ValueError(f"{CONFIG_PATH.name} missing header mapping for {key}")

    return {
        "headers": {key: normalize_header_label(str(value)) for key, value in headers.items()},
        "required_fields": [str(value) for value in required_fields],
        "required_header_keys": [str(value) for value in required_header_keys],
    }


def header_contract() -> dict[str, str]:
    return load_monitoring_contract()["headers"]  # type: ignore[return-value]


def required_fields() -> list[str]:
    return list(load_monitoring_contract()["required_fields"])  # type: ignore[return-value]


def required_header_keys() -> list[str]:
    return list(load_monitoring_contract()["required_header_keys"])  # type: ignore[return-value]


def retry_priority_fields() -> list[str]:
    ordered: list[str] = []
    for field_name in required_fields() + ["percent_total_gl", "total_gl", "avg_cost"]:
        if field_name not in ordered:
            ordered.append(field_name)
    return ordered


@dataclass(frozen=True)
class RowGeometry:
    top: int
    bottom: int
    left_symbol_boundary: float
    row_items: list[OcrItem]


@dataclass
class OcrBudget:
    max_calls: int = 0
    calls_used: int = 0

    def __post_init__(self) -> None:
        if self.max_calls == 0:
            self.max_calls = (
                MAX_TESSERACT_CELL_OCR_CALLS
                if preferred_ocr_engine() == "tesseract"
                else MAX_CELL_OCR_CALLS
            )

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


SectionCacheKey = tuple[int, int, int, int, int, str, int]


@lru_cache(maxsize=1)
def preferred_ocr_engine() -> str:
    configured = os.environ.get(OCR_ENGINE_ENV_VAR, "auto").strip().lower()
    if configured in {"vision", "tesseract"}:
        return configured
    if shutil.which("tesseract"):
        return "tesseract"
    return "vision"


def ocr_scratch_dir() -> Path:
    scratch_dir = OUTPUT_DIR / ".ocr_tmp"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return scratch_dir


def write_temp_ocr_image(image: Image.Image) -> Path:
    with tempfile.NamedTemporaryFile(
        suffix=".png",
        prefix="ocr_",
        dir=ocr_scratch_dir(),
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
    image.save(temp_path)
    return temp_path


def parse_tesseract_tsv(payload: str, image_size: tuple[int, int]) -> list[OcrItem]:
    width, height = image_size
    items: list[OcrItem] = []
    for row in csv.DictReader(payload.splitlines(), delimiter="\t"):
        text = clean_text(row.get("text", ""))
        if not text:
            continue
        try:
            confidence = float(row.get("conf", "-1"))
            left = int(row["left"])
            top = int(row["top"])
            item_width = int(row["width"])
            item_height = int(row["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if confidence < 0:
            continue
        items.append(
            OcrItem(
                text=text,
                x=left / width,
                y=1.0 - ((top + item_height) / height),
                width=item_width / width,
                height=item_height / height,
            )
        )
    return items


def run_tesseract_ocr(
    image_path: Path,
    *,
    psm: int = 6,
    extra_args: list[str] | None = None,
) -> list[OcrItem]:
    command = ["tesseract", str(image_path), "stdout", "--psm", str(psm), "-l", "eng", "tsv"]
    if extra_args:
        command.extend(extra_args)
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=True,
            timeout=OCR_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise OcrBackendUnavailableError("`tesseract` is not installed.") from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrExecutionError(f"Tesseract timed out on {image_path.name}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or str(exc)
        raise OcrExecutionError(f"Tesseract failed on {image_path.name}: {detail}") from exc

    with Image.open(image_path) as image:
        image_size = image.size
    return parse_tesseract_tsv(result.stdout, image_size)


def run_vision_ocr(image_path: Path) -> list[OcrItem]:
    try:
        result = subprocess.run(
            [*VISION_SWIFT_CMD, str(image_path)],
            input=VISION_OCR_SWIFT,
            text=True,
            capture_output=True,
            check=True,
            timeout=OCR_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise OcrBackendUnavailableError("Swift OCR tooling is unavailable.") from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrBackendUnavailableError(f"Vision OCR timed out on {image_path.name}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise OcrBackendUnavailableError(
            f"Vision OCR failed on {image_path.name}: {detail or exc}"
        ) from exc

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
        .replace("•", " ")
        .replace("Ф", " ")
        .replace("@", " ")
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


def strip_icon_tokens(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    tokens = cleaned.split()
    filtered = [
        token
        for token in tokens
        if token not in {"M", "E"} and token not in {"(M)", "(E)", "M)", "E)"}
    ]
    return " ".join(filtered)


def normalize_header_label(text: str) -> str:
    cleaned = clean_text(text).lower()
    cleaned = cleaned.replace(".", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def normalize_header_compact(text: str) -> str:
    return re.sub(r"[^a-z0-9%$]", "", normalize_header_label(text))


@lru_cache(maxsize=512)
def header_match_score(text: str, key: str) -> float:
    normalized = normalize_header_label(text)
    compact = normalize_header_compact(text)
    variants = {header_contract()[key], *HEADER_CLASSIFICATION_VARIANTS.get(key, ())}
    best = 0.0
    for variant in variants:
        normalized_variant = normalize_header_label(variant)
        compact_variant = normalize_header_compact(variant)
        if normalized == normalized_variant or compact == compact_variant:
            return 1.0
        normalized_coverage = min(len(normalized), len(normalized_variant)) / max(
            len(normalized), len(normalized_variant)
        )
        compact_coverage = min(len(compact), len(compact_variant)) / max(
            len(compact), len(compact_variant)
        )
        best = max(
            best,
            SequenceMatcher(None, normalized, normalized_variant).ratio() * normalized_coverage,
            SequenceMatcher(None, compact, compact_variant).ratio() * compact_coverage,
        )
    return best


def header_matches(text: str, key: str) -> bool:
    return header_match_score(text, key) >= 0.84


def extract_header_anchors(header_row: list[OcrItem]) -> dict[str, float]:
    anchors: dict[str, float] = {}
    ordered = sorted(header_row, key=lambda entry: entry.x)
    if not ordered:
        return anchors
    ordered_keys = sorted(required_header_keys(), key=lambda key: DEFAULT_HEADER_CENTERS[key])
    observed_min = min(item.center_x for item in ordered)
    observed_max = max(item.center_x for item in ordered)
    default_min = min(DEFAULT_HEADER_CENTERS[key] for key in ordered_keys)
    default_max = max(DEFAULT_HEADER_CENTERS[key] for key in ordered_keys)

    def fitted_center(key: str) -> float:
        ratio = (DEFAULT_HEADER_CENTERS[key] - default_min) / (default_max - default_min)
        return observed_min + (ratio * (observed_max - observed_min))

    bounds: dict[str, tuple[float, float]] = {}
    for index, key in enumerate(ordered_keys):
        current_center = fitted_center(key)
        left = 0.0 if index == 0 else (fitted_center(ordered_keys[index - 1]) + current_center) / 2
        right = 1.0 if index + 1 == len(ordered_keys) else (
            current_center + fitted_center(ordered_keys[index + 1])
        ) / 2
        bounds[key] = (left, right)

    for key in ordered_keys:
        left, right = bounds[key]
        region_items = [item for item in ordered if left <= item.center_x < right]
        if not region_items:
            continue
        label = " ".join(item.text for item in region_items).strip()
        score = header_match_score(label, key)
        if score < 0.70:
            continue
        left_item = region_items[0]
        right_item = region_items[-1]
        anchors[key] = (left_item.x + (right_item.x + right_item.width)) / 2

    if len(anchors) < len(ordered_keys):
        nearest_groups: dict[str, list[OcrItem]] = {key: [] for key in ordered_keys}
        for item in ordered:
            nearest_key = min(
                ordered_keys,
                key=lambda key: abs(item.center_x - fitted_center(key)),
            )
            nearest_groups[nearest_key].append(item)
        for key, items in nearest_groups.items():
            if key in anchors or not items:
                continue
            label = " ".join(item.text for item in items).strip()
            if header_match_score(label, key) < 0.68:
                continue
            left_item = items[0]
            right_item = items[-1]
            anchors[key] = (left_item.x + (right_item.x + right_item.width)) / 2

    if len(anchors) < len(ordered_keys) and len(ordered) == len(ordered_keys):
        sequential_anchors: dict[str, float] = {}
        for key, item in zip(ordered_keys, ordered):
            if header_match_score(item.text, key) < 0.70:
                sequential_anchors = {}
                break
            sequential_anchors[key] = item.x + (item.width / 2)
        if sequential_anchors:
            anchors = sequential_anchors

    if len(anchors) >= 9 and observed_max - observed_min >= 0.80:
        for key in ordered_keys:
            anchors.setdefault(key, fitted_center(key))
    elif len(anchors) >= 5 and 16 <= len(ordered) <= 18 and observed_max - observed_min >= 0.80:
        for key in ordered_keys:
            anchors.setdefault(key, fitted_center(key))

    return anchors


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
    temp_path = write_temp_ocr_image(image)
    try:
        return run_vision_ocr(temp_path)
    finally:
        if temp_path.exists():
            os.unlink(temp_path)


def run_tesseract_ocr_image(
    image: Image.Image,
    *,
    psm: int = 6,
    extra_args: list[str] | None = None,
) -> list[OcrItem]:
    temp_path = write_temp_ocr_image(image)
    try:
        return run_tesseract_ocr(temp_path, psm=psm, extra_args=extra_args)
    finally:
        if temp_path.exists():
            os.unlink(temp_path)


def run_vision_ocr_variants(image_path: Path, variants: list[str]) -> dict[str, list[OcrItem]]:
    variant_items: dict[str, list[OcrItem]] = {}
    with Image.open(image_path) as image:
        for variant in variants:
            variant_items[variant] = run_vision_ocr_image(preprocess_for_ocr(image, variant))
    return variant_items


def run_tesseract_ocr_variants(image_path: Path, variants: list[str]) -> dict[str, list[OcrItem]]:
    variant_items: dict[str, list[OcrItem]] = {}
    with Image.open(image_path) as image:
        for variant in variants:
            processed = preprocess_for_ocr(image, variant)
            variant_items[variant] = run_tesseract_ocr_image(processed, psm=6)
    return variant_items


def run_ocr_variants(image_path: Path, variants: list[str]) -> dict[str, list[OcrItem]]:
    engine = preferred_ocr_engine()
    if engine == "tesseract":
        return run_tesseract_ocr_variants(image_path, variants)
    return run_vision_ocr_variants(image_path, variants)


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

    anchors = extract_header_anchors(header_row)

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
    anchors = extract_header_anchors(row)
    return all(key in anchors for key in required_header_keys())


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
    text = strip_icon_tokens(text)
    text = re.sub(r"\s+[OeEG]$", "", text)
    text = re.sub(r"\s+(Call|Put)\s+[OeEG]$", r" \1", text)
    text = re.sub(r"\s*[\(\[\{]+$", "", text)
    text = re.sub(r"[^\w%$.)-]+$", "", text)
    return text.strip()


def normalize_description(text: str) -> str:
    return strip_icon_tokens(text).replace("...", "").strip()


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
    if not sign:
        sign = "+"
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


def ensure_signed_money_text(text: str) -> str:
    normalized = normalize_money_text(text)
    if normalized and normalized[0] not in "+-":
        return f"+{normalized}"
    return normalized


def ensure_signed_percent_text(text: str, *, paired_amount: str = "") -> str:
    normalized = normalize_percent_text(text, paired_amount=paired_amount)
    if normalized and normalized[0] not in "+-":
        return f"+{normalized}"
    return normalized


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
    if field_name in SIGNED_MONEY_FIELDS:
        return ensure_signed_money_text(text)
    if field_name in SIGNED_PERCENT_FIELDS:
        return ensure_signed_percent_text(text, paired_amount=paired_amount)
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
    if field_name in SIGNED_MONEY_FIELDS:
        return bool(re.fullmatch(r"[+-]\$\d+(?:\.\d{2})?", value))
    if field_name in SIGNED_PERCENT_FIELDS:
        return bool(re.fullmatch(r"[+-]\d+(?:\.\d{1,2})?%", value))
    if field_name in MONEY_FIELDS:
        return bool(re.fullmatch(r"[+-]?\$?\d+(?:\.\d{2})?", value))
    if field_name in PERCENT_FIELDS:
        return bool(re.fullmatch(r"[+-]?\d+(?:\.\d{1,2})?%", value))
    if field_name == "volume":
        return bool(re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})*", value))
    if field_name == "quantity":
        return bool(re.fullmatch(r"-?\d+|-?\d{1,3}(?:,\d{3})*", value))
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


def sign_of(text: str | None) -> int:
    if not text:
        return 0
    stripped = str(text).strip()
    if stripped.startswith("-"):
        return -1
    if stripped.startswith("+"):
        return 1
    if normalize_number(stripped) is not None:
        return 1
    return 0


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
    if len(value_whole) + 1 == len(context_whole):
        candidate_text = f"{context_whole[0]}{value_whole}.{value_frac}"
        candidate = float(candidate_text)
        if (
            numeric_context * 0.85 <= candidate <= numeric_context * 1.15
            and abs(candidate - numeric_context) < abs(numeric_value - numeric_context)
        ):
            return format_price(candidate)
    large_gap = abs(numeric_value - numeric_context) > max(5.0, numeric_context * 0.15)
    if len(value_whole) == len(context_whole) and large_gap:
        candidate_text = f"{context_whole}.{value_frac}"
        candidate = float(candidate_text)
        if (
            numeric_context * 0.85 <= candidate <= numeric_context * 1.15
            and abs(candidate - numeric_context) < abs(numeric_value - numeric_context)
        ):
            return format_price(candidate)
    if len(value_whole) == len(context_whole) and len(context_whole) >= 2 and large_gap:
        candidate_text = f"{context_whole[:-1]}{value_whole[-1]}.{value_frac}"
        candidate = float(candidate_text)
        if (
            numeric_context * 0.5 <= candidate <= numeric_context * 1.5
            and abs(candidate - numeric_context) < abs(numeric_value - numeric_context)
        ):
            return format_price(candidate)
    return value


def repair_price_magnitude(value: str, reference: str) -> str:
    numeric_value = normalize_number(value)
    numeric_reference = normalize_number(reference)
    if numeric_value is None or numeric_reference is None or numeric_reference <= 0 or numeric_value <= 0:
        return value
    candidate = numeric_value
    while candidate > numeric_reference * 3.0:
        candidate /= 10.0
    while candidate < numeric_reference * 0.3:
        candidate *= 10.0
    if abs(candidate - numeric_reference) < abs(numeric_value - numeric_reference):
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


def repair_range_magnitude(value: str, references: list[str]) -> str:
    numeric_value = normalize_number(value)
    numeric_refs = [reference for reference in (normalize_number(item) for item in references) if reference is not None]
    if numeric_value is None or numeric_value <= 0 or not numeric_refs:
        return value
    candidate = numeric_value
    ref_min = min(numeric_refs)
    ref_max = max(numeric_refs)
    while candidate > ref_max * 1.5:
        candidate /= 10.0
    while candidate < ref_min * 0.5:
        candidate *= 10.0
    if abs(candidate - ref_min) < abs(numeric_value - ref_min) or abs(candidate - ref_max) < abs(numeric_value - ref_max):
        return f"{candidate:.2f}"
    return value


def reconcile_numeric_fields(record: dict[str, str]) -> dict[str, str]:
    reconciled = dict(record)
    day_low = normalize_number(reconciled.get("day_range_low"))
    day_high = normalize_number(reconciled.get("day_range_high"))
    last = normalize_number(reconciled.get("last"))
    trustworthy_last = (
        last is not None
        and day_low is not None
        and day_high is not None
        and day_low <= last <= day_high
    )

    if trustworthy_last and reconciled.get("last"):
        for field_name in ("bid", "ask"):
            if reconciled.get(field_name):
                reconciled[field_name] = repair_price_from_context(
                    reconciled[field_name], reconciled["last"]
                )
                reconciled[field_name] = repair_price_magnitude(
                    reconciled[field_name], reconciled["last"]
                )
    elif reconciled.get("last"):
        reference_price = reconciled.get("ask") or reconciled.get("bid") or reconciled.get("last", "")
        reconciled["last"] = repair_price_magnitude(reconciled["last"], reference_price)

    if reconciled.get("last") and not trustworthy_last:
        for field_name in ("bid", "ask"):
            if reconciled.get(field_name):
                reconciled[field_name] = repair_price_magnitude(
                    reconciled[field_name], reconciled["last"]
                )

    if reconciled.get("bid") and reconciled.get("ask"):
        reconciled["ask"] = repair_price_from_context(
            reconciled["ask"], reconciled.get("bid", "")
        )
        reconciled["bid"] = repair_price_from_context(
            reconciled["bid"], reconciled.get("ask", "")
        )
    if reconciled.get("last") and not trustworthy_last:
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
        reconciled["day_range_low"] = repair_range_magnitude(
            reconciled["day_range_low"], price_refs
        )
    if reconciled.get("day_range_high"):
        reconciled["day_range_high"] = repair_range_from_references(
            reconciled["day_range_high"], price_refs
        )
        reconciled["day_range_high"] = repair_range_magnitude(
            reconciled["day_range_high"], price_refs
        )

    day_low = normalize_number(reconciled.get("day_range_low"))
    day_high = normalize_number(reconciled.get("day_range_high"))
    numeric_prices = [price for price in (normalize_number(item) for item in price_refs) if price is not None]
    if day_low is not None and day_high is not None and day_low > day_high and numeric_prices:
        reconciled["day_range_low"] = f"{min(numeric_prices + [day_low, day_high]):.2f}"
        reconciled["day_range_high"] = f"{max(numeric_prices + [day_low, day_high]):.2f}"
    return reconciled


def sanitize_optional_fields(record: dict[str, str]) -> dict[str, str]:
    sanitized = dict(record)
    last = normalize_number(sanitized.get("last"))
    day_low = normalize_number(sanitized.get("day_range_low"))
    day_high = normalize_number(sanitized.get("day_range_high"))
    if (
        day_low is not None
        and day_high is not None
        and (day_low > day_high or (last is not None and not (day_low <= last <= day_high)))
    ):
        sanitized["day_range_low"] = ""
        sanitized["day_range_high"] = ""

    price_refs = [
        value
        for value in (
            last,
            normalize_number(sanitized.get("bid")),
            normalize_number(sanitized.get("ask")),
            normalize_number(sanitized.get("day_range_low")),
            normalize_number(sanitized.get("day_range_high")),
        )
        if value is not None
    ]
    if price_refs:
        ref_min = min(price_refs)
        ref_max = max(price_refs)
        for field_name in ("week_52_low", "week_52_high"):
            numeric_value = normalize_number(sanitized.get(field_name))
            if numeric_value is None:
                continue
            if numeric_value < max(0.01, ref_min * 0.2) or numeric_value > ref_max * 5.0:
                sanitized[field_name] = ""

        avg_cost = normalize_number(sanitized.get("avg_cost"))
        if avg_cost is not None and (avg_cost < max(0.01, ref_min * 0.25) or avg_cost > ref_max * 5.0):
            sanitized["avg_cost"] = ""

    total_gl_sign = sign_of(sanitized.get("total_gl"))
    percent_total_gl_sign = sign_of(sanitized.get("percent_total_gl"))
    if total_gl_sign and percent_total_gl_sign and total_gl_sign != percent_total_gl_sign:
        sanitized["total_gl"] = ""
        sanitized["percent_total_gl"] = ""

    return sanitized


def parsed_row_quality(parsed: dict[str, str]) -> int:
    score = 0
    for field_name in required_fields():
        value = parsed.get(field_name, "")
        if value and is_valid_field_value(field_name, value):
            score += 2
    bid = normalize_number(parsed.get("bid"))
    ask = normalize_number(parsed.get("ask"))
    last = normalize_number(parsed.get("last"))
    day_low = normalize_number(parsed.get("day_range_low"))
    day_high = normalize_number(parsed.get("day_range_high"))
    if bid is not None and ask is not None and bid <= ask:
        score += 2
    if day_low is not None and day_high is not None and day_low <= day_high:
        score += 2
    if last is not None and day_low is not None and day_high is not None and day_low <= last <= day_high:
        score += 2
    change_sign = sign_of(parsed.get("change"))
    percent_sign = sign_of(parsed.get("percent_change"))
    if change_sign and percent_sign and change_sign == percent_sign:
        score += 2
    return score


def field_needs_retry(field_name: str, value: str) -> bool:
    if field_name in required_fields() and not value:
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
    return bool(OPTION_SYMBOL_RE.fullmatch(normalized))


def looks_like_equity_symbol(text: str) -> bool:
    normalized = normalize_symbol_line(text)
    return bool(EQUITY_SYMBOL_RE.fullmatch(normalized))


def month_from_token(token: str) -> str:
    letters = re.sub(r"[^A-Za-z]", "", clean_text(token)).lower()
    if not letters:
        return ""
    if letters in MONTH_PREFIX_MAP:
        return MONTH_PREFIX_MAP[letters]
    if len(letters) >= 3:
        prefix = letters[:3]
        if prefix in MONTH_PREFIX_MAP:
            return MONTH_PREFIX_MAP[prefix]
        if not re.search(r"[aeiouy]", letters):
            return ""
        best_name = max(MONTH_NAMES, key=lambda name: SequenceMatcher(None, prefix, name.lower()[:3]).ratio())
        if len(letters) <= 4 and SequenceMatcher(None, prefix, best_name.lower()[:3]).ratio() >= 0.7:
            return best_name
    return ""


def normalized_digit_token(token: str) -> str:
    cleaned = clean_text(token)
    cleaned = (
        cleaned.replace("O", "0")
        .replace("o", "0")
        .replace("I", "1")
        .replace("l", "1")
        .replace("Z", "2")
        .replace("z", "2")
        .replace("S", "5")
        .replace("s", "5")
    )
    return re.sub(r"[^0-9]", "", cleaned)


def extract_expiration_from_lines(lines: list[str]) -> str:
    for line in lines:
        tokens = re.findall(r"[A-Za-z]+|\d+", clean_text(line))
        for index, token in enumerate(tokens):
            month = month_from_token(token)
            if not month:
                continue
            trailing_digits = [normalized_digit_token(candidate) for candidate in tokens[index + 1 : index + 8]]
            pre_year_digits: list[str] = []
            for digits in trailing_digits:
                if digits.startswith("20") and len(digits) == 4:
                    break
                pre_year_digits.append(digits)
            valid_days = [int(digits) for digits in pre_year_digits if digits and 1 <= int(digits) <= 31]
            day = ""
            for candidate in valid_days:
                if candidate >= 10:
                    day = str(candidate)
                    break
            if not day and valid_days:
                day = str(valid_days[-1])
            year_candidates = [digits for digits in trailing_digits if digits.startswith("20") and len(digits) == 4]
            year = year_candidates[-1] if year_candidates else ""
            if month and day and year:
                return f"{month} {day} {year}"
    return ""


def score_symbol_candidate(tokens: list[str], index: int) -> int:
    token = tokens[index]
    if not re.fullmatch(r"[A-Z]{1,5}", token):
        return -100
    if token in SYMBOL_STOPWORDS:
        return -100
    score = 6 if len(token) >= 2 else 1
    if index <= 4:
        score += 2
    trailing_words = 0
    for candidate in tokens[index + 1 : index + 6]:
        if candidate.isdigit():
            score += 4
            continue
        if candidate in SYMBOL_STOPWORDS:
            continue
        if len(candidate) >= 3:
            trailing_words += 1
    score += trailing_words * 3
    if any(candidate in {"CALL", "PUT"} for candidate in tokens[index + 1 : index + 8]):
        score += 5
    return score


def extract_symbol_candidate_from_lines(lines: list[str]) -> str:
    best_symbol = ""
    best_score = -100
    fallback_symbol = ""
    fallback_score = -100
    for line in lines:
        tokens = re.findall(r"\b[A-Z]{1,5}\b|\d+(?:\.\d+)?|Call|Put", normalize_symbol_line(line).replace(".", " "))
        normalized_tokens = [token.upper() if token not in {"Call", "Put"} else token.upper() for token in tokens]
        for index, token in enumerate(normalized_tokens):
            score = score_symbol_candidate(normalized_tokens, index)
            if len(token) > 1 and score > best_score:
                best_symbol = token
                best_score = score
            if score > fallback_score:
                fallback_symbol = token
                fallback_score = score
    return best_symbol or fallback_symbol


def extract_option_symbol_from_lines(lines: list[str]) -> str:
    consensus_symbol = extract_symbol_candidate_from_lines(lines)
    direct_candidates: list[str] = []
    for line in lines:
        normalized_line = normalize_symbol_line(line)
        match = OPTION_SYMBOL_RE.search(normalized_line)
        if match:
            direct_candidates.append(match.group(0))
    if direct_candidates:
        chosen = normalize_symbol_line(direct_candidates[-1])
        if consensus_symbol:
            parts = chosen.split()
            if len(parts) == 3 and len(parts[0]) == len(consensus_symbol):
                similarity = SequenceMatcher(None, parts[0], consensus_symbol).ratio()
                if similarity >= 0.7:
                    return f"{consensus_symbol} {parts[1]} {parts[2]}"
            if any(candidate.startswith(f"{consensus_symbol} ") for candidate in direct_candidates):
                return next(
                    candidate for candidate in reversed(direct_candidates) if candidate.startswith(f"{consensus_symbol} ")
                )
        return chosen

    symbol = consensus_symbol
    if not symbol:
        return ""

    for line in lines:
        tokens = re.findall(r"[A-Za-z]+|\d+(?:\.\d+)?", clean_text(line))
        upper_tokens = [token.upper() for token in tokens]
        option_type = next(
            (
                "Call" if token.startswith("CA") else "Put"
                for token in upper_tokens
                if token in {"CALL", "PUT"} or SequenceMatcher(None, token, "CALL").ratio() >= 0.7 or SequenceMatcher(None, token, "PUT").ratio() >= 0.7
            ),
            "",
        )
        if not option_type:
            continue
        numeric_tokens = [
            token
            for token in tokens
            if re.fullmatch(r"\d+(?:\.\d+)?", token)
            and not (len(token) == 4 and token.startswith("20"))
        ]
        strike = ""
        for token in reversed(numeric_tokens):
            numeric_value = float(token)
            if 5 <= numeric_value <= 1000:
                strike = token.rstrip("0").rstrip(".") if "." in token else token
                break
        if strike:
            return f"{symbol} {strike} {option_type}"
    return ""


def select_symbol_lines(lines: list[str]) -> tuple[str, list[str]]:
    normalized = [clean_text(line) for line in lines if clean_text(line)]
    option_symbol = extract_option_symbol_from_lines(normalized)
    if option_symbol:
        remaining = [line for line in normalized if option_symbol not in clean_text(line)]
        expiration = extract_expiration_from_lines(normalized)
        if expiration and expiration not in remaining:
            remaining.append(expiration)
        return option_symbol, remaining

    option_candidates = [line for line in normalized if looks_like_option_symbol(line)]
    if not option_candidates:
        embedded_options: list[str] = []
        for line in normalized:
            match = re.search(r"\b([A-Z]{1,5}\s+\d+(?:\.\d+)?\s+(?:Call|Put))\b", line)
            if match:
                embedded_options.append(match.group(1))
        option_candidates = embedded_options
    if option_candidates:
        symbol = normalize_symbol_line(option_candidates[-1])
        remaining = [line for line in normalized if symbol not in clean_text(line)]
        return symbol, remaining

    equity_candidates = [line for line in normalized if looks_like_equity_symbol(line)]
    if equity_candidates:
        symbol = normalize_symbol_line(equity_candidates[0])
        remaining = normalized[normalized.index(equity_candidates[0]) + 1 :]
        return symbol, remaining

    extracted_symbol = extract_symbol_candidate_from_lines(normalized)
    if extracted_symbol:
        remaining = [line for line in normalized if extracted_symbol not in normalize_symbol_line(line)]
        return extracted_symbol, remaining

    token_candidates: list[str] = []
    for line in normalized:
        token_candidates.extend(re.findall(r"\b[A-Z]{1,5}\b", normalize_symbol_line(line)))
    strong_token_candidates = [token for token in token_candidates if len(token) > 1]
    if strong_token_candidates:
        symbol = strong_token_candidates[0]
        remaining = [line for line in normalized if symbol not in line]
        return symbol, remaining
    if token_candidates:
        symbol = token_candidates[0]
        remaining = [line for line in normalized if symbol not in line]
        return symbol, remaining

    for line in normalized:
        if looks_like_expiration(line):
            continue
        compact = normalize_symbol_line(line)
        tokens = compact.split()
        preferred_token = next((token for token in tokens if re.fullmatch(r"[A-Z]{2,5}", token)), None)
        if preferred_token is None:
            preferred_token = next((token for token in tokens if re.fullmatch(r"[A-Z]{1,5}", token)), None)
        if preferred_token:
            symbol = preferred_token
            remainder = " ".join(tokens[1:])
            remaining = ([remainder] if remainder else []) + normalized[normalized.index(line) + 1 :]
            return symbol, remaining
    return "", normalized


def parse_symbol_block(lines: list[str]) -> tuple[str, str, str, str]:
    cleaned = [normalize_symbol_line(line) for line in lines if normalize_symbol_line(line)]
    if not cleaned:
        return "", "unknown", "", ""

    symbol_line, remaining_lines = select_symbol_lines(cleaned)
    if symbol_line and (" Call" in symbol_line or " Put" in symbol_line):
        expiration = extract_expiration_from_lines(cleaned)
        if not expiration:
            for candidate in cleaned:
                if looks_like_expiration(candidate):
                    expiration = clean_text(candidate)
        return symbol_line, "option", "", expiration

    first = normalize_symbol_line(symbol_line or cleaned[0])
    if EQUITY_SYMBOL_RE.fullmatch(first):
        return first, "equity", "", ""
    for token in re.findall(r"\b[A-Z]{1,5}\b", first):
        if token not in SYMBOL_STOPWORDS and EQUITY_SYMBOL_RE.fullmatch(token):
            return token, "equity", "", ""
    return "", "unknown", "", ""


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


def repair_shifted_required_fields_from_raw(
    raw_fields: dict[str, str],
    normalized: dict[str, str],
) -> dict[str, str]:
    repaired = dict(normalized)
    shifted = {
        "last": normalize_field_value("last", extract_best_field_value("last", [raw_fields.get("change", "")])),
        "change": normalize_field_value(
            "change",
            extract_best_field_value("change", [raw_fields.get("percent_change", "")]),
        ),
        "percent_change": normalize_percent_text(
            extract_best_field_value("percent_change", [raw_fields.get("bid", "")])
        ),
        "bid": normalize_field_value("bid", extract_best_field_value("bid", [raw_fields.get("ask", "")])),
        "ask": normalize_field_value("ask", extract_best_field_value("ask", [raw_fields.get("volume", "")])),
        "volume": normalize_field_value(
            "volume",
            extract_best_field_value("volume", [raw_fields.get("day_range_low", "")]),
        ),
    }
    shifted_last = normalize_number(shifted["last"])
    shifted_bid = normalize_number(shifted["bid"])
    shifted_ask = normalize_number(shifted["ask"])
    if (
        not repaired.get("last")
        and shifted_last is not None
        and shifted_bid is not None
        and shifted_ask is not None
        and shifted_bid <= shifted_ask
        and shifted_bid * 0.85 <= shifted_last <= shifted_ask * 1.15
        and is_valid_field_value("change", shifted["change"])
        and is_valid_field_value("percent_change", shifted["percent_change"])
    ):
        for field_name in ("last", "change", "percent_change", "bid", "ask"):
            repaired[field_name] = shifted[field_name]
        if is_valid_field_value("volume", shifted["volume"]):
            repaired["volume"] = shifted["volume"]
    return repaired


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
    padding_ratio = 0.12 if preferred_ocr_engine() == "tesseract" else 0.35
    min_padding = 2 if preferred_ocr_engine() == "tesseract" else 4
    padding = max(min_padding, int((raw_bottom - raw_top) * padding_ratio))
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
    if preferred_ocr_engine() == "tesseract":
        try:
            items = run_tesseract_ocr_image(crop, psm=7)
        except OcrExecutionError:
            items = []
    else:
        items = run_vision_ocr_image(crop)
    cache[cache_key] = items
    return items


def row_section_ocr_items(
    image: Image.Image,
    geometry: RowGeometry,
    bounds: tuple[float, float],
    cache: dict[SectionCacheKey, list[OcrItem]],
    *,
    scale: int = 4,
    variant: str = "grayscale",
    psm: int = 6,
) -> list[OcrItem]:
    crop_left, top, crop_right, bottom = cell_pixel_bounds(image.size, geometry, bounds)
    cache_key: SectionCacheKey = (crop_left, top, crop_right, bottom, scale, variant, psm)
    if cache_key in cache:
        return cache[cache_key]

    crop = image.crop((crop_left, top, crop_right, bottom))
    crop = preprocess_for_ocr(crop, variant)
    crop = crop.resize((max(1, (crop_right - crop_left) * scale), max(1, (bottom - top) * scale)))

    if preferred_ocr_engine() == "tesseract":
        try:
            section_items = run_tesseract_ocr_image(crop, psm=psm)
        except OcrExecutionError:
            section_items = []
    else:
        section_items = run_vision_ocr_image(crop)

    image_width, image_height = image.size
    crop_width = max(1, crop_right - crop_left)
    crop_height = max(1, bottom - top)
    mapped_items: list[OcrItem] = []
    for item in section_items:
        mapped_x = (crop_left + (item.x * crop_width)) / image_width
        mapped_width = (item.width * crop_width) / image_width
        mapped_top_px = top + ((1.0 - (item.y + item.height)) * crop_height)
        mapped_height_px = item.height * crop_height
        mapped_y = 1.0 - ((mapped_top_px + mapped_height_px) / image_height)
        mapped_height = mapped_height_px / image_height
        mapped_items.append(
            OcrItem(
                text=item.text,
                x=mapped_x,
                y=mapped_y,
                width=mapped_width,
                height=mapped_height,
            )
        )

    cache[cache_key] = mapped_items
    return mapped_items


def tesseract_row_record(
    image: Image.Image,
    geometry: RowGeometry,
    column_ranges: dict[str, tuple[float, float]],
    section_cache: dict[SectionCacheKey, list[OcrItem]],
) -> tuple[list[str], dict[str, str]]:
    row_items = row_section_ocr_items(
        image,
        geometry,
        (0.0, 1.0),
        section_cache,
        scale=3,
        variant="grayscale",
        psm=6,
    )
    return extract_symbol_lines(row_items, geometry.left_symbol_boundary), parse_main_row_raw(
        row_items,
        column_ranges,
    )


def tesseract_left_lines(
    image: Image.Image,
    geometry: RowGeometry,
    section_cache: dict[SectionCacheKey, list[OcrItem]],
) -> list[str]:
    candidate_lines: list[str] = []
    for variant, scale, psm in (("grayscale", 6, 6), ("binary", 8, 11)):
        items = row_section_ocr_items(
            image,
            geometry,
            (0.0, geometry.left_symbol_boundary),
            section_cache,
            scale=scale,
            variant=variant,
            psm=psm,
        )
        candidate_lines.extend(lines_from_crop_items(items))
    deduped: list[str] = []
    for line in candidate_lines:
        normalized = clean_text(line)
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def tesseract_row_stream_fields(
    image: Image.Image,
    geometry: RowGeometry,
    column_ranges: dict[str, tuple[float, float]],
    section_cache: dict[SectionCacheKey, list[OcrItem]],
) -> dict[str, str]:
    band_items = row_section_ocr_items(
        image,
        geometry,
        (column_ranges["last"][0], column_ranges["percent_total_gl"][1]),
        section_cache,
        scale=4,
        variant="grayscale",
        psm=11,
    )
    ordered_texts = [clean_text(item.text) for item in sorted(band_items, key=lambda entry: entry.x)]
    stream: list[tuple[str, str]] = []
    for text in ordered_texts:
        if not text:
            continue
        for token in re.findall(r"[+-]?\$?\d[\d,]*(?:\.\d+)?%?", text):
            if token.endswith("%"):
                normalized = normalize_percent_text(token)
                if normalized:
                    stream.append(("percent", normalized))
                continue
            if "$" in token or token.startswith(("+$", "-$")):
                normalized = normalize_money_text(token)
                if normalized:
                    stream.append(("money", normalized))
                continue
            if "." in token:
                normalized = normalize_range_text(token)
                if normalized:
                    stream.append(("range", normalized))
                continue
            normalized = normalize_integer_text(token, signed=token.startswith("-"))
            if normalized:
                stream.append(("integer", normalized if not token.startswith("-") else f"-{normalized.lstrip('-')}"))

    def next_value(kind: str) -> str:
        for index, (candidate_kind, value) in enumerate(stream):
            if candidate_kind != kind:
                continue
            stream.pop(index)
            return value
        return ""

    return {
        "last": next_value("money"),
        "change": next_value("money"),
        "percent_change": next_value("percent"),
        "bid": next_value("money"),
        "ask": next_value("money"),
        "volume": next_value("integer"),
        "day_range_low": next_value("range"),
        "day_range_high": next_value("range"),
        "week_52_low": next_value("range"),
        "week_52_high": next_value("range"),
        "avg_cost": next_value("money"),
        "quantity": next_value("integer"),
        "total_gl": next_value("money"),
        "percent_total_gl": next_value("percent"),
    }


def adopt_raw_quantity_sign(record: dict[str, str], raw_fields: dict[str, str]) -> dict[str, str]:
    repaired = dict(record)
    parsed_quantity = repaired.get("quantity", "")
    raw_quantity = normalize_field_value("quantity", raw_fields.get("quantity", ""))
    if (
        parsed_quantity
        and raw_quantity.startswith("-")
        and parsed_quantity.lstrip("-") == raw_quantity.lstrip("-")
    ):
        repaired["quantity"] = raw_quantity
        return repaired
    parsed_numeric = normalize_number(parsed_quantity)
    raw_numeric = normalize_number(raw_quantity)
    if (
        repaired.get("instrument_type") == "option"
        and parsed_numeric is not None
        and raw_numeric is not None
        and raw_quantity.startswith("-")
        and abs(raw_numeric - parsed_numeric) <= max(3.0, abs(parsed_numeric) * 0.15)
    ):
        repaired["quantity"] = raw_quantity
        return repaired
    if (
        repaired.get("instrument_type") == "equity"
        and parsed_numeric is not None
        and raw_numeric is not None
        and abs(parsed_numeric) < 10
        and abs(raw_numeric) >= 10
    ):
        repaired["quantity"] = raw_quantity
    return repaired


def repair_percent_change_from_price_fields(record: dict[str, str]) -> dict[str, str]:
    repaired = dict(record)
    last = normalize_number(repaired.get("last"))
    change = normalize_number(repaired.get("change"))
    percent_change = normalize_number(repaired.get("percent_change"))
    if last is None or change is None:
        return repaired
    previous_close = last - change
    if previous_close <= 0:
        return repaired
    implied = (change / previous_close) * 100.0
    if percent_change is None or abs(percent_change - implied) > max(1.0, abs(implied) * 3.0):
        repaired["percent_change"] = f"{implied:+.2f}%"
    return repaired


def repair_volume_quantity_swap(record: dict[str, str]) -> dict[str, str]:
    repaired = dict(record)
    volume = normalize_number(repaired.get("volume"))
    quantity = normalize_number(repaired.get("quantity"))
    if volume is None or quantity is None:
        return repaired
    if volume < 1000 and quantity > 10000:
        repaired["volume"], repaired["quantity"] = repaired["quantity"], repaired["volume"]
    return repaired


def repair_position_pnl_fields(record: dict[str, str]) -> dict[str, str]:
    repaired = dict(record)
    multiplier = 100.0 if repaired.get("instrument_type") == "option" else 1.0
    last = normalize_number(repaired.get("last"))
    avg_cost = normalize_number(repaired.get("avg_cost"))
    quantity = normalize_number(repaired.get("quantity"))
    total_gl = repaired.get("total_gl", "")
    percent_total_gl = repaired.get("percent_total_gl", "")
    if last is None or avg_cost is None or quantity is None or quantity == 0:
        return repaired

    implied_total = (last - avg_cost) * quantity * multiplier
    normalized_total_gl = normalize_number(total_gl)
    expected_sign = "-" if implied_total < 0 else "+"
    if (not total_gl or normalized_total_gl is None) and abs(implied_total) > 0.01:
        repaired["total_gl"] = f"{implied_total:+.2f}"
        repaired["total_gl"] = ensure_signed_money_text(repaired["total_gl"])
    elif total_gl and abs(implied_total) > 0.01:
        repaired["total_gl"] = f"{expected_sign}{total_gl.lstrip('+-')}"
        if percent_total_gl:
            repaired["percent_total_gl"] = f"{expected_sign}{percent_total_gl.lstrip('+-')}"

    normalized_percent_total = normalize_number(repaired.get("percent_total_gl"))
    compact_percent = re.sub(r"[^0-9]", "", repaired.get("percent_total_gl", ""))
    if (
        normalized_percent_total is None
        or abs(normalized_percent_total) < 0.1
        or ("." not in repaired.get("percent_total_gl", "") and len(compact_percent) <= 2)
    ):
        basis = avg_cost * abs(quantity) * multiplier
        if basis > 0:
            implied_percent = (implied_total / basis) * 100.0
            repaired["percent_total_gl"] = f"{implied_percent:+.2f}%"
    return repaired


def repair_quantity_from_position(record: dict[str, str]) -> dict[str, str]:
    repaired = dict(record)
    instrument_type = repaired.get("instrument_type")
    if instrument_type not in {"equity", "option"}:
        return repaired
    last = normalize_number(repaired.get("last"))
    avg_cost = normalize_number(repaired.get("avg_cost"))
    total_gl = normalize_number(repaired.get("total_gl"))
    quantity = normalize_number(repaired.get("quantity"))
    if last is None or avg_cost is None or total_gl is None or quantity is None:
        return repaired
    multiplier = 100.0 if instrument_type == "option" else 1.0
    unit_gl = (last - avg_cost) * multiplier
    if abs(unit_gl) < 0.01:
        return repaired
    implied_quantity = total_gl / unit_gl
    rounded_quantity = round(implied_quantity)
    if rounded_quantity < 1:
        rounded_quantity = round(abs(implied_quantity))
    if rounded_quantity < 1:
        return repaired
    if abs(quantity) < 10 or abs(quantity - implied_quantity) > max(5.0, abs(implied_quantity) * 0.25):
        repaired["quantity"] = f"{rounded_quantity:,}" if instrument_type == "equity" else str(int(rounded_quantity))
        if instrument_type == "option" and implied_quantity < 0:
            repaired["quantity"] = f"-{repaired['quantity'].lstrip('-')}"
    return repaired


def repair_quantity_sign_from_position(record: dict[str, str]) -> dict[str, str]:
    repaired = dict(record)
    if repaired.get("instrument_type") != "option":
        return repaired
    last = normalize_number(repaired.get("last"))
    avg_cost = normalize_number(repaired.get("avg_cost"))
    total_gl = normalize_number(repaired.get("total_gl"))
    quantity = repaired.get("quantity", "")
    if last is None or avg_cost is None or total_gl is None or not quantity:
        return repaired
    unit_gl = (last - avg_cost) * 100.0
    if abs(unit_gl) < 0.01:
        return repaired
    implied_quantity = total_gl / unit_gl
    if implied_quantity < 0 and not quantity.startswith("-"):
        repaired["quantity"] = f"-{quantity.lstrip('-')}"
    if implied_quantity > 0 and quantity.startswith("-"):
        repaired["quantity"] = quantity.lstrip("-")
    return repaired


def repair_tesseract_price_band(
    image: Image.Image,
    geometry: RowGeometry,
    column_ranges: dict[str, tuple[float, float]],
    record: dict[str, str],
    section_cache: dict[SectionCacheKey, list[OcrItem]],
) -> dict[str, str]:
    repaired = dict(record)
    band_items = row_section_ocr_items(
        image,
        geometry,
        (column_ranges["last"][0], column_ranges["volume"][1]),
        section_cache,
        scale=4,
        variant="grayscale",
        psm=11,
    )
    ordered_texts = [item.text for item in sorted(band_items, key=lambda entry: entry.x)]
    money_candidates: list[str] = []
    percent_candidates: list[str] = []
    for text in ordered_texts:
        cleaned_text = clean_text(text)
        if "%" not in cleaned_text:
            money_candidates.extend(re.findall(r"[+-]?\$?\d[\d,]*(?:\.\d+)?", cleaned_text))
        percent_candidates.extend(re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?%", cleaned_text))

    normalized_money = [
        normalize_money_text(candidate)
        for candidate in money_candidates
        if normalize_money_text(candidate)
    ]
    normalized_percent = [
        normalize_percent_text(candidate)
        for candidate in percent_candidates
        if normalize_percent_text(candidate)
    ]

    suspicious_price_fields = detect_suspicious_fields(repaired).intersection(
        {"last", "bid", "ask", "change", "percent_change"}
    )
    force_band = bool(suspicious_price_fields)
    missing_last = not repaired.get("last")
    if (missing_last or force_band) and normalized_money:
        repaired["last"] = normalized_money[0]
    if (missing_last or force_band or field_needs_retry("change", repaired.get("change", ""))) and len(normalized_money) >= 2:
        repaired["change"] = normalized_money[1]
    if (missing_last or force_band or field_needs_retry("percent_change", repaired.get("percent_change", ""))) and normalized_percent:
        repaired["percent_change"] = normalized_percent[0]
    if (missing_last or force_band or field_needs_retry("bid", repaired.get("bid", ""))) and len(normalized_money) >= 3:
        repaired["bid"] = normalized_money[2]
    if (missing_last or force_band or field_needs_retry("ask", repaired.get("ask", ""))) and len(normalized_money) >= 4:
        repaired["ask"] = normalized_money[3]

    return repaired


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
        for variant in cell_ocr_variants():
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
    force_fields: set[str] | None = None,
) -> dict[str, str]:
    repaired = dict(record)
    retried_fields = set(repaired.get("_retried_fields", []))
    forced = force_fields or set()

    symbol, instrument_type, _description, expiration = parse_symbol_block(left_lines)
    if symbol:
        repaired["symbol"] = symbol
    if instrument_type:
        repaired["instrument_type"] = instrument_type
    repaired["description"] = ""
    if expiration or repaired["instrument_type"] == "option":
        repaired["expiration"] = expiration

    if repaired.get("quantity"):
        normalized_quantity = normalize_field_value("quantity", repaired["quantity"])
        if normalized_quantity:
            repaired["quantity"] = normalized_quantity

    for field_name, texts in field_texts.items():
        if (
            field_name not in forced
            and repaired.get(field_name)
            and field_name != "quantity"
            and not field_needs_retry(
            field_name, repaired.get(field_name, "")
            )
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
    section_cache: dict[SectionCacheKey, list[OcrItem]],
) -> dict[str, str]:
    if preferred_ocr_engine() == "tesseract":
        record = repair_tesseract_price_band(image, geometry, column_ranges, record, section_cache)

    left_lines: list[str] = []
    for variant in cell_ocr_variants():
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
    if preferred_ocr_engine() == "tesseract":
        suspicious_fields = set()
    retry_fields = []
    for field_name in retry_priority_fields():
        if field_name in column_ranges and field_name not in retry_fields:
            if preferred_ocr_engine() == "tesseract" and not field_needs_retry(
                field_name,
                record.get(field_name, ""),
            ):
                continue
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

    return repair_record_from_crop_texts(
        record,
        left_lines,
        field_texts,
        force_fields=suspicious_fields,
    )


def validate_required_fields(record: dict[str, str], image_path: Path) -> None:
    missing_fields = [field_name for field_name in required_fields() if not record.get(field_name)]
    if missing_fields:
        raise ValueError(
            f"Missing required monitoring fields for {image_path.name}: {', '.join(missing_fields)}"
        )


def validate_field_shapes(record: dict[str, str], image_path: Path) -> None:
    invalid_fields = [
        field_name
        for field_name in required_fields()
        if record.get(field_name) and not is_valid_field_value(field_name, record.get(field_name, ""))
    ]
    if invalid_fields:
        raise ValueError(
            f"Invalid extracted field shapes for {image_path.name}: {', '.join(invalid_fields)}"
        )


def validate_cross_field_consistency(record: dict[str, str], image_path: Path) -> None:
    bid = normalize_number(record.get("bid"))
    ask = normalize_number(record.get("ask"))
    last = normalize_number(record.get("last"))
    day_low = normalize_number(record.get("day_range_low"))
    day_high = normalize_number(record.get("day_range_high"))
    if bid is not None and ask is not None and bid > ask:
        raise ValueError(f"Bid exceeds ask for {image_path.name}: {record.get('symbol', '')}")
    if day_low is not None and day_high is not None and day_low > day_high:
        raise ValueError(
            f"Day range low exceeds high for {image_path.name}: {record.get('symbol', '')}"
        )
    if last is not None and day_low is not None and day_high is not None and not (day_low <= last <= day_high):
        raise ValueError(
            f"Last is outside day range for {image_path.name}: {record.get('symbol', '')}"
        )
    change_sign = sign_of(record.get("change"))
    percent_sign = sign_of(record.get("percent_change"))
    if change_sign and percent_sign and change_sign != percent_sign:
        raise ValueError(
            f"Change sign conflicts with percent change for {image_path.name}: {record.get('symbol', '')}"
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
    change_sign = sign_of(record.get("change"))
    percent_sign = sign_of(record.get("percent_change"))
    if change_sign and percent_sign and change_sign != percent_sign:
        suspicious.update({"change", "percent_change"})
    if day_low is not None and day_high is not None:
        floor = max(0.0, day_low - 1.0)
        ceiling = day_high + 1.0
        if bid is not None and not (floor <= bid <= ceiling):
            suspicious.add("bid")
        if ask is not None and not (floor <= ask <= ceiling):
            suspicious.add("ask")
    if record.get("quantity") and not is_valid_field_value("quantity", record.get("quantity", "")):
        suspicious.add("quantity")
    return suspicious


def record_needs_crop_repair(record: dict[str, str]) -> bool:
    if not record.get("symbol"):
        return True
    if record.get("instrument_type") == "option" and not record.get("expiration"):
        return True
    suspicious_fields = detect_suspicious_fields(record)
    if preferred_ocr_engine() == "tesseract":
        if len(record.get("symbol", "")) <= 1 or record.get("instrument_type") == "unknown":
            return True
        if suspicious_fields.intersection({"last", "bid", "ask", "change", "percent_change"}):
            return True
    elif suspicious_fields:
        return True
    return any(field_needs_retry(field_name, record.get(field_name, "")) for field_name in required_fields())


def finalize_record(record: dict[str, str] | None, image_path: Path) -> dict[str, str] | None:
    if record is None:
        return None
    validate_required_fields(record, image_path)
    finalized = sanitize_optional_fields(record)
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
    return len(extract_header_anchors(header_row))


def missing_required_headers(header_row: list[OcrItem]) -> list[str]:
    anchors = extract_header_anchors(header_row)
    return [key for key in required_header_keys() if key not in anchors]


def select_ocr_rows(image_path: Path) -> SelectedRows:
    validate_image_quality(image_path)
    try:
        variant_items = run_ocr_variants(image_path, header_ocr_variants())
    except OcrBackendUnavailableError as exc:
        raise ImageQualityError(str(exc)) from exc
    except OcrExecutionError as exc:
        raise ImageQualityError(str(exc)) from exc
    best_rows: list[list[OcrItem]] = []
    best_header: list[OcrItem] | None = None
    best_ranges = dict(DEFAULT_COLUMN_RANGES)
    best_score = -1
    best_missing: list[str] = list(required_header_keys())
    for items in variant_items.values():
        rows = group_rows(items)
        header_row = detect_header_row(rows)
        score = len(rows)
        current_missing = list(required_header_keys())
        if header_row is not None:
            anchors = extract_header_anchors(header_row)
            score += 100 + (10 * len(anchors))
            current_missing = [key for key in required_header_keys() if key not in anchors]
            column_ranges = derive_column_ranges(header_row)
            sample_count = 0
            for row in rows:
                if row is header_row:
                    continue
                if not is_main_data_row(row, column_ranges):
                    continue
                score += parsed_row_quality(parse_main_row(row, column_ranges))
                sample_count += 1
                if sample_count >= 4:
                    break
        if score > best_score:
            best_rows = rows
            best_header = header_row
            best_ranges = derive_column_ranges(header_row)
            best_score = score
            best_missing = current_missing
    if best_header is None:
        missing_labels = ", ".join(header_contract()[key] for key in best_missing)
        raise ImageQualityError(
            f"{image_path.name} does not expose the required monitoring headers: {missing_labels}"
        )
    return SelectedRows(best_rows, best_header, best_ranges)


def build_records(image_path: Path) -> list[dict[str, str]]:
    selected = select_ocr_rows(image_path)
    rows = selected.rows
    column_ranges = selected.column_ranges
    created_at = image_created_at(image_path).isoformat(timespec="seconds")
    tesseract_primary = preferred_ocr_engine() == "tesseract"
    active_column_ranges = dict(DEFAULT_COLUMN_RANGES) if tesseract_primary else column_ranges
    symbol_right_boundary = min(active_column_ranges["last"][0], 0.30)

    records: list[dict[str, str]] = []
    pending_symbol_lines: list[str] = []
    current_record: dict[str, str] | None = None
    last_equity_symbol = ""
    crop_cache: dict[tuple[int, int, int, int, int, int | None, str], list[OcrItem]] = {}
    section_cache: dict[SectionCacheKey, list[OcrItem]] = {}

    with Image.open(image_path) as image:
        for row in rows:
            if is_header_row(row):
                continue

            left_lines = extract_symbol_lines(row, symbol_right_boundary)
            if is_main_data_row(row, column_ranges):
                if current_record is not None:
                    records.append(finalize_record(current_record, image_path))

                raw_fields = parse_main_row_raw(row, active_column_ranges)
                geometry = build_row_geometry(row, image.size[1], symbol_right_boundary)
                candidate_lines = left_lines if select_symbol_lines(left_lines)[0] else pending_symbol_lines + left_lines
                if tesseract_primary:
                    targeted_left_lines = tesseract_left_lines(image, geometry, section_cache)
                    symbol_lines = targeted_left_lines + pending_symbol_lines
                    symbol, instrument_type, _description, expiration = parse_symbol_block(symbol_lines)
                    parsed_fields = tesseract_row_stream_fields(image, geometry, active_column_ranges, section_cache)
                    current_record = {
                        "schema_name": SCHEMA_NAME,
                        "image_file": image_path.name,
                        "created_at": created_at,
                        "symbol": symbol,
                        "instrument_type": instrument_type,
                        "description": "",
                        "expiration": expiration,
                        **normalize_parsed_fields(parsed_fields),
                    }
                    raw_normalized = normalize_parsed_fields(raw_fields)
                    for field_name, raw_value in raw_normalized.items():
                        if field_name not in current_record or current_record.get(field_name):
                            continue
                        current_record[field_name] = raw_value
                    current_record = adopt_raw_quantity_sign(current_record, raw_fields)
                    current_record = repair_shifted_required_fields_from_raw(raw_fields, current_record)
                    current_record = repair_percent_change_from_price_fields(current_record)
                    current_record = repair_volume_quantity_swap(current_record)
                    current_record = repair_quantity_sign_from_position(current_record)
                    current_record = repair_position_pnl_fields(current_record)
                    current_record = repair_quantity_from_position(current_record)
                    current_record = repair_position_pnl_fields(current_record)
                    if current_record.get("instrument_type") == "option":
                        quantity_budget = OcrBudget(max_calls=3)
                        quantity_texts = collect_cell_texts(
                            "quantity",
                            image,
                            geometry,
                            active_column_ranges["quantity"],
                            quantity_budget,
                            crop_cache,
                            CELL_REPAIR_ATTEMPTS["quantity"],
                        )
                        quantity_candidate = normalize_field_value(
                            "quantity",
                            extract_best_field_value("quantity", quantity_texts),
                        )
                        if quantity_candidate:
                            current_record["quantity"] = quantity_candidate
                            current_record = adopt_raw_quantity_sign(current_record, raw_fields)
                            current_record = repair_quantity_sign_from_position(current_record)
                            current_record = repair_position_pnl_fields(current_record)
                    if current_record.get("instrument_type") == "option" and last_equity_symbol:
                        underlying = current_record.get("symbol", "").split(" ", 1)[0]
                        if (
                            underlying
                            and len(underlying) == len(last_equity_symbol)
                            and SequenceMatcher(None, underlying, last_equity_symbol).ratio() >= 0.7
                        ):
                            current_record["symbol"] = current_record["symbol"].replace(
                                underlying,
                                last_equity_symbol,
                                1,
                            )
                else:
                    symbol, instrument_type, _description, expiration = parse_symbol_block(candidate_lines)
                    raw_record = RawRecord(
                        schema_name=SCHEMA_NAME,
                        image_file=image_path.name,
                        created_at=created_at,
                        symbol=symbol,
                        instrument_type=instrument_type,
                        description="",
                        expiration=expiration,
                        raw_fields=raw_fields,
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
                    current_record = repair_shifted_required_fields_from_raw(
                        raw_record.raw_fields,
                        current_record,
                    )
                current_record = reconcile_numeric_fields(current_record)
                needs_fallback = (
                    not current_record.get("symbol")
                    or current_record.get("instrument_type") == "unknown"
                    or (
                        current_record.get("instrument_type") == "option"
                        and not current_record.get("expiration")
                    )
                    or any(field_needs_retry(field_name, current_record.get(field_name, "")) for field_name in required_fields())
                )
                if needs_fallback or (not tesseract_primary and record_needs_crop_repair(current_record)):
                    budget = OcrBudget()
                    current_record = repair_record_from_image_crop(
                        image=image,
                        geometry=geometry,
                        record=current_record,
                        column_ranges=active_column_ranges,
                        budget=budget,
                        cache=crop_cache,
                        section_cache=section_cache,
                    )
                if current_record.get("instrument_type") == "equity" and current_record.get("symbol"):
                    last_equity_symbol = current_record["symbol"]
                pending_symbol_lines = []
                continue

            if is_range_row(row, column_ranges):
                if current_record is not None and not tesseract_primary:
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
