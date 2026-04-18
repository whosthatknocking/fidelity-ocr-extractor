import json
from pathlib import Path
import io
from contextlib import redirect_stderr
from contextlib import redirect_stdout
import tempfile
import unittest
from unittest import mock

import extract as extractor
from PIL import Image


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def sample_created_at() -> extractor.datetime:
    return extractor.datetime.fromisoformat("2030-01-02T03:04:05-08:00")


def resize_x(x_value: float) -> float:
    return (0.82 * x_value) + 0.06


class ExtractorHelperTests(unittest.TestCase):
    def test_header_matching_classifies_known_ocr_misses(self) -> None:
        self.assertTrue(extractor.header_matches("Symbol", "symbol"))
        self.assertTrue(extractor.header_matches("% Change", "percent_change"))
        self.assertTrue(extractor.header_matches("Avg. cost", "avg_cost"))
        self.assertTrue(extractor.header_matches("SymDol", "symbol"))
        self.assertTrue(extractor.header_matches("Acl", "ask"))
        self.assertTrue(extractor.header_matches("Voluime", "volume"))
        self.assertFalse(extractor.header_matches("Random Header", "volume"))

    def test_preferred_ocr_engine_prefers_tesseract_when_available(self) -> None:
        extractor.preferred_ocr_engine.cache_clear()
        with mock.patch.dict("os.environ", {}, clear=False):
            with mock.patch("extract.shutil.which", return_value="/opt/homebrew/bin/tesseract"):
                self.assertEqual(extractor.preferred_ocr_engine(), "tesseract")
        extractor.preferred_ocr_engine.cache_clear()

    def test_preferred_ocr_engine_honors_explicit_override(self) -> None:
        extractor.preferred_ocr_engine.cache_clear()
        with mock.patch.dict("os.environ", {extractor.OCR_ENGINE_ENV_VAR: "vision"}, clear=False):
            self.assertEqual(extractor.preferred_ocr_engine(), "vision")
        extractor.preferred_ocr_engine.cache_clear()

    def test_left_text_ocr_variants_include_inverted_tesseract_variants(self) -> None:
        extractor.preferred_ocr_engine.cache_clear()
        with mock.patch.dict("os.environ", {extractor.OCR_ENGINE_ENV_VAR: "tesseract"}, clear=False):
            self.assertEqual(
                extractor.left_text_ocr_variants(),
                (
                    ("grayscale", 6, 6),
                    ("invert_grayscale", 6, 6),
                    ("binary", 8, 11),
                    ("invert_binary", 8, 11),
                ),
            )
        extractor.preferred_ocr_engine.cache_clear()

    def test_preprocess_for_ocr_supports_inverted_variants(self) -> None:
        image = Image.new("L", (1, 1), color=32)

        inverted = extractor.preprocess_for_ocr(image, "invert_grayscale")
        inverted_binary = extractor.preprocess_for_ocr(image, "invert_binary")

        self.assertEqual(inverted.getpixel((0, 0)), 223)
        self.assertEqual(inverted_binary.getpixel((0, 0)), 255)

    def test_parse_tesseract_tsv_converts_to_normalized_items(self) -> None:
        payload = "\n".join(
            [
                "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                "5\t1\t1\t1\t1\t1\t20\t10\t40\t20\t91.5\tUBER",
                "5\t1\t1\t1\t1\t2\t80\t10\t30\t20\t88.0\t80",
                "5\t1\t1\t1\t1\t3\t120\t10\t40\t20\t85.0\tCall",
            ]
        )

        items = extractor.parse_tesseract_tsv(payload, (200, 100))

        self.assertEqual([item.text for item in items], ["UBER", "80", "Call"])
        self.assertAlmostEqual(items[0].x, 0.10)
        self.assertAlmostEqual(items[0].y, 0.70)
        self.assertAlmostEqual(items[0].width, 0.20)
        self.assertAlmostEqual(items[0].height, 0.20)

    def test_monitoring_contract_loads_required_fields_from_toml(self) -> None:
        self.assertEqual(
            extractor.required_fields(),
            ["symbol", "last", "change", "percent_change", "bid", "ask", "quantity"],
        )
        self.assertEqual(
            extractor.required_header_keys(),
            [
                "symbol",
                "last",
                "change",
                "percent_change",
                "bid",
                "ask",
                "volume",
                "day_range",
                "week_52_range",
                "avg_cost",
                "quantity",
                "total_gl",
                "percent_total_gl",
            ],
        )

    def test_quantity_normalization_strips_margin_glyph_noise(self) -> None:
        self.assertEqual(extractor.extract_best_field_value("quantity", ["-4 M"]), "-4")
        self.assertEqual(extractor.extract_best_field_value("quantity", ["-1Ф"]), "-1")
        self.assertEqual(extractor.extract_best_field_value("quantity", ["100 M"]), "100")

    def test_numeric_field_prefers_full_decimal_candidate(self) -> None:
        self.assertEqual(
            extractor.extract_best_field_value("day_range_low", ["2", "362.50"]),
            "362.50",
        )
        self.assertEqual(
            extractor.extract_best_field_value("week_52_high", ["28.00"]),
            "28.00",
        )

    def test_extract_best_field_value_compacts_spaced_numeric_fragments(self) -> None:
        self.assertEqual(
            extractor.extract_best_field_value("total_gl", ["+$2 459.00"]),
            "+$2459.00",
        )
        self.assertEqual(
            extractor.extract_best_field_value("percent_total_gl", ["+179 94%"]),
            "+17994%",
        )
        self.assertEqual(
            extractor.extract_best_field_value("week_52_low", ["222 79"]),
            "222.79",
        )

    def test_money_and_integer_normalizers_repair_ocr_noise(self) -> None:
        self.assertEqual(extractor.normalize_money_text("S0K6"), "$0.06")
        self.assertEqual(extractor.normalize_money_text("-$2 281.50"), "-$2281.50")
        self.assertEqual(extractor.normalize_integer_text("3.191 887"), "3,191,887")
        self.assertEqual(extractor.repair_price_from_context("$70.40", "$170.60"), "$170.40")
        self.assertEqual(extractor.repair_price_from_context("$336.10", "$337.12"), "$336.10")
        self.assertEqual(extractor.normalize_field_value("change", "$0.65"), "+$0.65")
        self.assertEqual(extractor.normalize_field_value("percent_change", "1.00%"), "+1.00%")
        self.assertTrue(extractor.field_needs_retry("bid", "Act"))
        self.assertFalse(extractor.field_needs_retry("bid", "$64.83"))

    def test_reconcile_numeric_fields_repairs_shifted_digits(self) -> None:
        reconciled = extractor.reconcile_numeric_fields(
            {
                "last": "$391.95",
                "bid": "$4202.12",
                "ask": "$420.22",
                "day_range_low": "391.95",
                "day_range_high": "394.65",
            }
        )
        self.assertEqual(reconciled["bid"], "$420.21")
        reconciled = extractor.reconcile_numeric_fields(
            {
                "last": "$3849.85",
                "bid": "$3849.50",
                "ask": "$385.00",
                "day_range_low": "38.26",
                "day_range_high": "394.06",
            }
        )
        self.assertEqual(reconciled["last"], "$384.99")
        self.assertEqual(reconciled["bid"], "$384.95")
        self.assertEqual(reconciled["day_range_low"], "382.60")
        reconciled = extractor.reconcile_numeric_fields(
            {
                "last": "$197.78",
                "bid": "$197.78",
                "ask": "$107.70",
                "day_range_low": "334.52",
                "day_range_high": "39.88",
            }
        )
        self.assertEqual(reconciled["ask"], "$197.78")
        reconciled = extractor.reconcile_numeric_fields(
            {
                "last": "$3335.08",
                "bid": "$335.08",
                "ask": "$335.11",
                "day_range_low": "334.52",
                "day_range_high": "39.88",
            }
        )
        self.assertNotEqual(reconciled["last"], "$3335.08")
        self.assertGreater(extractor.normalize_number(reconciled["last"]), 300)
        self.assertLess(extractor.normalize_number(reconciled["last"]), 400)
        self.assertEqual(reconciled["day_range_high"], "339.88")
        reconciled = extractor.reconcile_numeric_fields(
            {
                "last": "$0.65",
                "bid": "$0.64",
                "ask": "$0.65",
                "day_range_low": "0.88",
                "day_range_high": "0.05",
            }
        )
        self.assertLessEqual(
            extractor.normalize_number(reconciled["day_range_low"]),
            extractor.normalize_number(reconciled["day_range_high"]),
        )
        self.assertGreaterEqual(extractor.normalize_number(reconciled["day_range_low"]), 0.0)
        self.assertLessEqual(extractor.normalize_number(reconciled["day_range_high"]), 1.0)
        reconciled = extractor.reconcile_numeric_fields(
            {
                "last": "$39.85",
                "bid": "$32.54",
                "ask": "$49.03",
                "day_range_low": "38.99",
                "day_range_high": "39.99",
            }
        )
        self.assertEqual(reconciled["last"], "$39.85")
        self.assertEqual(reconciled["bid"], "$39.54")
        self.assertGreaterEqual(extractor.normalize_number(reconciled["ask"]), 39.0)
        self.assertLessEqual(extractor.normalize_number(reconciled["ask"]), 40.0)

    def test_normalize_parsed_fields_uses_regression_fixture(self) -> None:
        fixture = json.loads((FIXTURES_DIR / "noisy_option_row.json").read_text(encoding="utf-8"))
        normalized = extractor.normalize_parsed_fields(fixture["raw_fields"])
        for key, value in fixture["expected"].items():
            self.assertEqual(normalized[key], value)

    def test_percent_normalization_repairs_missing_decimal_and_sign(self) -> None:
        self.assertEqual(extractor.normalize_percent_text("+599%"), "+5.99%")
        self.assertEqual(extractor.normalize_percent_text("+179 94%"), "+179.94%")
        self.assertEqual(extractor.normalize_percent_text("-1,90%"), "-1.90%")
        self.assertEqual(
            extractor.normalize_percent_text("22.12%", paired_amount="-$20,189.95"),
            "-22.12%",
        )

    def test_range_normalization_repairs_missing_decimal_and_ocr_noise(self) -> None:
        self.assertEqual(extractor.normalize_range_text("6846"), "68.46")
        self.assertEqual(extractor.normalize_range_text("12124"), "121.24")
        self.assertEqual(extractor.normalize_range_text("222 79"), "222.79")
        self.assertEqual(extractor.normalize_range_text("029"), "0.29")
        self.assertEqual(extractor.normalize_range_text("*.5A"), "0.54")
        self.assertEqual(extractor.normalize_range_text("725"), "7.25")
        self.assertEqual(extractor.normalize_range_text("77.936846"), "77.93")

    def test_repair_record_from_crop_texts_updates_symbol_and_missing_fields(self) -> None:
        record = {
            "schema_name": "monitoring",
            "image_file": "fixture_input.png",
            "created_at": "2030-01-02T03:04:05-08:00",
            "symbol": "HHRSR",
            "instrument_type": "equity",
            "description": "ROCSIl",
            "expiration": "",
            "last": "$4.30",
            "change": "",
            "percent_change": "+0.84%",
            "bid": "$4.10",
            "ask": "$4.30",
            "volume": "1,178",
            "day_range_low": "",
            "day_range_high": "4.55",
            "week_52_low": "1.56",
            "week_52_high": "",
            "avg_cost": "$4.35",
            "quantity": "-18",
            "total_gl": "+$98.89",
            "percent_total_gl": "+1.26%",
        }

        repaired = extractor.repair_record_from_crop_texts(
            record=record,
            left_lines=["UBER 80 Call", "Jun 18 2026"],
            field_texts={
                "change": ["+$1.96"],
                "day_range_low": ["2.62"],
                "week_52_high": ["28.00"],
            },
        )

        self.assertEqual(repaired["symbol"], "UBER 80 Call")
        self.assertEqual(repaired["instrument_type"], "option")
        self.assertEqual(repaired["expiration"], "Jun 18 2026")
        self.assertEqual(repaired["description"], "")
        self.assertEqual(repaired["quantity"], "-18")
        self.assertEqual(repaired["change"], "+$1.96")
        self.assertEqual(repaired["day_range_low"], "2.62")
        self.assertEqual(repaired["week_52_high"], "28.00")
        self.assertIn("change", repaired["_retried_fields"])

    def test_repair_record_from_crop_texts_does_not_downgrade_instrument_type_to_unknown(self) -> None:
        repaired = extractor.repair_record_from_crop_texts(
            record={
                "schema_name": "monitoring",
                "image_file": "fixture_input.png",
                "created_at": "2030-01-02T03:04:05-08:00",
                "symbol": "UBER 78 Call",
                "instrument_type": "option",
                "description": "",
                "expiration": "May 1 2026",
                "last": "$2.04",
                "change": "+$0.44",
                "percent_change": "+27.50%",
                "bid": "$1.89",
                "ask": "$2.04",
                "volume": "204",
                "day_range_low": "",
                "day_range_high": "",
                "week_52_low": "",
                "week_52_high": "",
                "avg_cost": "",
                "quantity": "-1",
                "total_gl": "",
                "percent_total_gl": "",
            },
            left_lines=["—", "…"],
            field_texts={},
        )

        self.assertEqual(repaired["symbol"], "UBER 78 Call")
        self.assertEqual(repaired["instrument_type"], "option")
        self.assertEqual(repaired["expiration"], "May 1 2026")

    def test_repair_record_from_crop_texts_preserves_valid_symbol_against_bad_retry_symbol(self) -> None:
        repaired = extractor.repair_record_from_crop_texts(
            record={
                "schema_name": "monitoring",
                "image_file": "fixture_input.png",
                "created_at": "2030-01-02T03:04:05-08:00",
                "symbol": "UBER 78 Call",
                "instrument_type": "option",
                "description": "",
                "expiration": "May 1 2026",
                "last": "$2.04",
                "change": "+$0.44",
                "percent_change": "+27.50%",
                "bid": "$1.89",
                "ask": "$2.04",
                "volume": "204",
                "day_range_low": "",
                "day_range_high": "",
                "week_52_low": "",
                "week_52_high": "",
                "avg_cost": "",
                "quantity": "-1",
                "total_gl": "",
                "percent_total_gl": "",
            },
            left_lines=["AE"],
            field_texts={},
        )

        self.assertEqual(repaired["symbol"], "UBER 78 Call")
        self.assertEqual(repaired["instrument_type"], "option")
        self.assertEqual(repaired["expiration"], "May 1 2026")

    def test_parse_symbol_block_ignores_stale_expiration_prefix(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["May 15 2026", "FDIG", "FIDELITY CRYPTO INDUSTRY AND DIGITAL PAYMENTS ETF"]
        )
        self.assertEqual(symbol, "FDIG")
        self.assertEqual(instrument_type, "equity")
        self.assertEqual(description, "")
        self.assertEqual(expiration, "")

    def test_parse_symbol_block_prefers_latest_option_candidate(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["GOOGL 325 Put", "GOOGL 315 Put", "GOOGL 340 Call", "Aug 21 2026"]
        )
        self.assertEqual(symbol, "GOOGL 340 Call")
        self.assertEqual(instrument_type, "option")
        self.assertEqual(expiration, "Aug 21 2026")

    def test_select_symbol_lines_prefers_embedded_symbol_over_single_letter_noise(self) -> None:
        symbol, remaining = extractor.select_symbol_lines(
            ["M FBTC FIDELITY WISE ORIGIN BITCOIN FUND"]
        )
        self.assertEqual(symbol, "FBTC")
        self.assertEqual(remaining, [])

    def test_select_symbol_lines_extracts_embedded_option_symbol(self) -> None:
        symbol, remaining = extractor.select_symbol_lines(
            ["M FBTC 70 Call", "May 15 2026"]
        )
        self.assertEqual(symbol, "FBTC 70 Call")
        self.assertEqual(remaining, ["May 15 2026"])

    def test_parse_symbol_block_recovers_equity_symbol_from_noisy_description_line(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["wy rae FBTC FIDELITY WISE ORIGIN BITCOIN FUND"]
        )
        self.assertEqual(symbol, "FBTC")
        self.assertEqual(instrument_type, "equity")
        self.assertEqual(description, "")
        self.assertEqual(expiration, "")

    def test_parse_symbol_block_recovers_option_and_expiration_from_noisy_lines(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            [
                "FBTC TIVECLIIT May 15 70 2026 V¥IOE Call VNIOIN DIILVUIN FUINY",
                "G) = FETC 70 Call",
            ]
        )
        self.assertEqual(symbol, "FBTC 70 Call")
        self.assertEqual(instrument_type, "option")
        self.assertEqual(description, "")
        self.assertEqual(expiration, "May 15 2026")

    def test_parse_symbol_block_does_not_emit_raw_expiration_garbage(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            [
                "G) — PAL GOOGL Apr TRPADL 17 20264 325 I TINA. Put VPA OE LL",
                "GOOGL 325 Put",
            ]
        )
        self.assertEqual(symbol, "GOOGL 325 Put")
        self.assertEqual(instrument_type, "option")
        self.assertEqual(description, "")
        self.assertEqual(expiration, "")

    def test_parse_symbol_block_prefers_multi_letter_symbol_over_leading_noise(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["M om UBER UBER TECHNOLOGIES INC COM", "6 UIBER"]
        )
        self.assertEqual(symbol, "UBER")
        self.assertEqual(instrument_type, "equity")
        self.assertEqual(description, "")
        self.assertEqual(expiration, "")

    def test_parse_symbol_block_prefers_repeated_equity_ticker_over_noise_prefix(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["QO MSFT MICROSOFT CORP", "MSFT"]
        )
        self.assertEqual(symbol, "MSFT")
        self.assertEqual(instrument_type, "equity")
        self.assertEqual(description, "")
        self.assertEqual(expiration, "")

    def test_parse_symbol_block_prefers_ticker_before_company_name_over_noisy_standalone_line(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["NVDA NVIDIA CORPORATION COM", "NYDA"]
        )
        self.assertEqual(symbol, "NVDA")
        self.assertEqual(instrument_type, "equity")
        self.assertEqual(description, "")
        self.assertEqual(expiration, "")

    def test_parse_symbol_block_prefers_actual_ticker_over_company_name_token(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["TSLA TESLA INC COM"]
        )
        self.assertEqual(symbol, "TSLA")
        self.assertEqual(instrument_type, "equity")
        self.assertEqual(description, "")
        self.assertEqual(expiration, "")

    def test_parse_symbol_block_ignores_description_suffix_token_for_equity(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["PALANTIR TECHNOLOGIES INC CLA", "PLTR"]
        )
        self.assertEqual(symbol, "PLTR")
        self.assertEqual(instrument_type, "equity")
        self.assertEqual(description, "")
        self.assertEqual(expiration, "")

    def test_parse_symbol_block_prefers_inferred_option_strike_over_short_noisy_candidate(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            [
                "UBER May 01 78 2026 call",
                "UBER 73 Call",
            ]
        )
        self.assertEqual(symbol, "UBER 78 Call")
        self.assertEqual(instrument_type, "option")
        self.assertEqual(description, "")

    def test_normalize_symbol_line_ignores_icon_tokens(self) -> None:
        self.assertEqual(
            extractor.normalize_symbol_line("M FBTC E FIDELITY WISE ORIGIN BITCOIN FUND"),
            "FBTC FIDELITY WISE ORIGIN BITCOIN FUND",
        )

    def test_select_symbol_lines_uses_current_row_equity_symbol(self) -> None:
        symbol, remaining = extractor.select_symbol_lines(
            ["GOOGL", "ALPHABET INC CAP STK CL A"]
        )
        self.assertEqual(symbol, "GOOGL")
        self.assertEqual(remaining, ["ALPHABET INC CAP STK CL A"])

    def test_validate_cross_field_consistency_rejects_bid_above_ask(self) -> None:
        with self.assertRaisesRegex(ValueError, "Bid exceeds ask"):
            extractor.validate_cross_field_consistency(
                {
                    "symbol": "UBER",
                    "bid": "$77.00",
                    "ask": "$76.00",
                    "day_range_low": "73.79",
                    "day_range_high": "77.93",
                },
                Path("fixture_input.png"),
            )

    def test_detect_suspicious_fields_marks_inverted_bid_ask(self) -> None:
        suspicious = extractor.detect_suspicious_fields(
            {
                "last": "$197.78",
                "bid": "$197.78",
                "ask": "$107.70",
                "day_range_low": "195.81",
                "day_range_high": "199.85",
            }
        )
        self.assertIn("ask", suspicious)
        self.assertIn("bid", suspicious)

    def test_detect_suspicious_fields_marks_sign_conflict(self) -> None:
        suspicious = extractor.detect_suspicious_fields(
            {
                "change": "$4.25",
                "percent_change": "-3.1%",
            }
        )
        self.assertIn("change", suspicious)
        self.assertIn("percent_change", suspicious)

    def test_sanitize_optional_fields_blanks_implausible_values(self) -> None:
        sanitized = extractor.sanitize_optional_fields(
            {
                "last": "$64.82",
                "bid": "$64.83",
                "ask": "$64.84",
                "day_range_low": "63.74",
                "day_range_high": "65.33",
                "week_52_low": "5421110.25",
                "avg_cost": "$0.39",
                "total_gl": "+$163.83",
                "percent_total_gl": "-0.65%",
            }
        )
        self.assertEqual(sanitized["week_52_low"], "")
        self.assertEqual(sanitized["avg_cost"], "")
        self.assertEqual(sanitized["total_gl"], "")
        self.assertEqual(sanitized["percent_total_gl"], "")

    def test_sanitize_optional_fields_blanks_inconsistent_day_range(self) -> None:
        sanitized = extractor.sanitize_optional_fields(
            {
                "last": "$21.55",
                "day_range_low": "27.49",
                "day_range_high": "29.30",
            }
        )
        self.assertEqual(sanitized["day_range_low"], "")
        self.assertEqual(sanitized["day_range_high"], "")

    def test_repair_shifted_required_fields_from_raw_recovers_tesseract_shift(self) -> None:
        raw_fields = {
            "last": ": H —_",
            "change": "$65.36 aes",
            "percent_change": "Sweet +$0.65 ope el",
            "bid": "ere +1.00% ine nd me",
            "ask": "$65.01 aw",
            "volume": "$65.25 ou",
            "day_range_low": "3,188,082 Swewerss",
        }
        normalized = {
            "last": "",
            "change": "$65.36",
            "percent_change": "",
            "bid": "$1.00",
            "ask": "$65.01",
            "volume": "65,25",
        }

        repaired = extractor.repair_shifted_required_fields_from_raw(raw_fields, normalized)

        self.assertEqual(repaired["last"], "$65.36")
        self.assertEqual(repaired["change"], "+$0.65")
        self.assertEqual(repaired["percent_change"], "+1.00%")
        self.assertEqual(repaired["bid"], "$65.01")
        self.assertEqual(repaired["ask"], "$65.25")
        self.assertEqual(repaired["volume"], "3,188,082")

    def test_reconcile_numeric_fields_does_not_loop_on_zero_ranges(self) -> None:
        reconciled = extractor.reconcile_numeric_fields(
            {
                "last": "",
                "bid": "$1.00",
                "ask": "$65.01",
                "day_range_low": "3.10",
                "day_range_high": "0",
            }
        )
        self.assertEqual(reconciled["day_range_high"], "65.01")

    def test_repair_tesseract_price_band_recovers_shifted_first_columns(self) -> None:
        record = {
            "last": "",
            "change": "$05.36",
            "percent_change": "",
            "bid": "$1.00",
            "ask": "$65.01",
        }
        items = [
            extractor.OcrItem(text="$65.36", x=0.20, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+$0.65", x=0.27, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+1.00%", x=0.34, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$65.01", x=0.42, y=0.0, width=0.01, height=0.01),
        ]

        with mock.patch("extract.row_section_ocr_items", return_value=items):
            repaired = extractor.repair_tesseract_price_band(
                image=mock.Mock(),
                geometry=extractor.RowGeometry(0, 10, 0.2, []),
                column_ranges=extractor.DEFAULT_COLUMN_RANGES,
                record=record,
                section_cache={},
            )

        self.assertEqual(repaired["last"], "$65.36")
        self.assertEqual(repaired["change"], "+$0.65")
        self.assertEqual(repaired["percent_change"], "+1.00%")
        self.assertEqual(repaired["bid"], "$65.01")

    def test_repair_tesseract_price_band_uses_binary_option_band_when_grayscale_misses(self) -> None:
        grayscale_items: list[extractor.OcrItem] = []
        binary_items = [
            extractor.OcrItem(text="$2.04", x=0.24, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+$0.44", x=0.31, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+27.50%", x=0.37, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$1.89", x=0.44, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$2.04", x=0.49, y=0.0, width=0.01, height=0.01),
        ]

        with mock.patch("extract.row_section_ocr_items", side_effect=[grayscale_items, binary_items]):
            repaired = extractor.repair_tesseract_price_band(
                image=mock.Mock(),
                geometry=extractor.RowGeometry(0, 10, 0.2, []),
                column_ranges=extractor.DEFAULT_COLUMN_RANGES,
                record={
                    "instrument_type": "option",
                    "last": "$2.04",
                    "change": "",
                    "percent_change": "",
                    "bid": "",
                    "ask": "$2.04",
                },
                section_cache={},
            )

        self.assertEqual(repaired["change"], "+$0.44")
        self.assertEqual(repaired["percent_change"], "+27.50%")
        self.assertEqual(repaired["bid"], "$1.89")

    def test_detect_header_row_accepts_partial_top_header_row(self) -> None:
        partial_header = [
            extractor.OcrItem(text="Symbol", x=0.01, y=0.94, width=0.05, height=0.02),
            extractor.OcrItem(text="Last", x=0.20, y=0.94, width=0.04, height=0.02),
            extractor.OcrItem(text="Change", x=0.30, y=0.94, width=0.05, height=0.02),
            extractor.OcrItem(text="Quantity", x=0.82, y=0.94, width=0.07, height=0.02),
            extractor.OcrItem(text="Total", x=0.90, y=0.94, width=0.04, height=0.02),
            extractor.OcrItem(text="G/L", x=0.95, y=0.94, width=0.03, height=0.02),
        ]
        data_row = [
            extractor.OcrItem(text="UBER", x=0.01, y=0.90, width=0.04, height=0.02),
            extractor.OcrItem(text="$77.28", x=0.20, y=0.90, width=0.05, height=0.02),
        ]

        def fake_extract_header_anchors(row: list[extractor.OcrItem]) -> dict[str, float]:
            if row is partial_header:
                return {
                    "symbol": 0.03,
                    "last": 0.22,
                    "change": 0.32,
                    "quantity": 0.85,
                    "total_gl": 0.93,
                    "percent_total_gl": 0.97,
                }
            return {}

        with mock.patch("extract.extract_header_anchors", side_effect=fake_extract_header_anchors):
            detected = extractor.detect_header_row([partial_header, data_row])

        self.assertIs(detected, partial_header)

    def test_tesseract_row_stream_fields_maps_monitoring_columns_in_order(self) -> None:
        items = [
            extractor.OcrItem(text="$337.12", x=0.20, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+$4.21", x=0.27, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+1.26%", x=0.34, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$336.10", x=0.42, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$336.40", x=0.48, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="24,864,034", x=0.55, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="330.90", x=0.60, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="337.48", x=0.63, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="146.10", x=0.67, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="349.00", x=0.70, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$312.53", x=0.76, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="100", x=0.83, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+$2,459.00", x=0.90, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+7.87%", x=0.97, y=0.0, width=0.01, height=0.01),
        ]

        with mock.patch("extract.row_section_ocr_items", return_value=items):
            parsed = extractor.tesseract_row_stream_fields(
                image=mock.Mock(),
                geometry=extractor.RowGeometry(0, 10, 0.2, []),
                column_ranges=extractor.DEFAULT_COLUMN_RANGES,
                section_cache={},
            )

        self.assertEqual(parsed["last"], "$337.12")
        self.assertEqual(parsed["change"], "+$4.21")
        self.assertEqual(parsed["percent_change"], "+1.26%")
        self.assertEqual(parsed["bid"], "$336.10")
        self.assertEqual(parsed["ask"], "$336.40")
        self.assertEqual(parsed["volume"], "24,864,034")
        self.assertEqual(parsed["day_range_low"], "330.90")
        self.assertEqual(parsed["day_range_high"], "337.48")
        self.assertEqual(parsed["week_52_low"], "146.10")
        self.assertEqual(parsed["week_52_high"], "349.00")
        self.assertEqual(parsed["avg_cost"], "$312.53")
        self.assertEqual(parsed["quantity"], "100")
        self.assertEqual(parsed["total_gl"], "+$2459.00")
        self.assertEqual(parsed["percent_total_gl"], "+7.87%")

    def test_tesseract_row_stream_fields_uses_header_derived_schema_ranges(self) -> None:
        header_row = [
            extractor.OcrItem(text="Symbol", x=resize_x(0.04), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Last", x=resize_x(0.22), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Change", x=resize_x(0.29), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="%", x=resize_x(0.35), y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Change", x=resize_x(0.36), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Bid", x=resize_x(0.42), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Ask", x=resize_x(0.48), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Volume", x=resize_x(0.53), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Day", x=resize_x(0.585), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="range", x=resize_x(0.612), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="52-week", x=resize_x(0.655), y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="range", x=resize_x(0.695), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Avg Cost", x=resize_x(0.725), y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="Quantity", x=resize_x(0.805), y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="$", x=resize_x(0.86), y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Total", x=resize_x(0.875), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="G/L", x=resize_x(0.915), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="%", x=resize_x(0.95), y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Total", x=resize_x(0.962), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="G/L", x=resize_x(0.995), y=0.0, width=0.03, height=0.01),
        ]
        column_ranges = extractor.derive_column_ranges(header_row)
        items = [
            extractor.OcrItem(text="$337.12", x=resize_x(0.20), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="+$4.21", x=resize_x(0.27), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="+1.26%", x=resize_x(0.34), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="$336.10", x=resize_x(0.42), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="$336.40", x=resize_x(0.48), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="24,864,034", x=resize_x(0.55), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="330.90", x=resize_x(0.60), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="337.48", x=resize_x(0.63), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="146.10", x=resize_x(0.67), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="349.00", x=resize_x(0.70), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="$312.53", x=resize_x(0.76), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="-18", x=resize_x(0.83), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="+$2 459.00", x=resize_x(0.90), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="+7.87%", x=resize_x(0.97), y=0.0, width=0.02, height=0.01),
        ]

        with mock.patch("extract.row_section_ocr_items", return_value=items):
            parsed = extractor.tesseract_row_stream_fields(
                image=mock.Mock(),
                geometry=extractor.RowGeometry(0, 10, column_ranges["last"][0], []),
                column_ranges=column_ranges,
                section_cache={},
            )

        self.assertEqual(parsed["last"], "$337.12")
        self.assertEqual(parsed["change"], "+$4.21")
        self.assertEqual(parsed["percent_change"], "+1.26%")
        self.assertEqual(parsed["bid"], "$336.10")
        self.assertEqual(parsed["ask"], "$336.40")
        self.assertEqual(parsed["volume"], "24,864,034")
        self.assertEqual(parsed["day_range_low"], "330.90")
        self.assertEqual(parsed["day_range_high"], "337.48")
        self.assertEqual(parsed["week_52_low"], "146.10")
        self.assertEqual(parsed["week_52_high"], "349.00")
        self.assertEqual(parsed["avg_cost"], "$312.53")
        self.assertEqual(parsed["quantity"], "-18")
        self.assertEqual(parsed["total_gl"], "+$2459.00")
        self.assertEqual(parsed["percent_total_gl"], "+7.87%")

    def test_tesseract_row_stream_fields_prefers_sequential_change_when_schema_duplicates_last(self) -> None:
        items = [
            extractor.OcrItem(text="$0.78", x=0.20, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="-$0.26", x=0.28, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="-0.25%", x=0.34, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$0.76", x=0.42, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$0.78", x=0.48, y=0.0, width=0.01, height=0.01),
        ]

        with mock.patch("extract.row_section_ocr_items", return_value=items):
            with mock.patch(
                "extract.collect_schema_field_texts",
                return_value={
                    "last": ["$0.78"],
                    "change": ["+$0.78"],
                    "percent_change": ["-0.25%"],
                    "bid": ["$0.76"],
                    "ask": ["$0.76"],
                    "volume": [],
                    "day_range_low": [],
                    "day_range_high": [],
                    "week_52_low": [],
                    "week_52_high": [],
                    "avg_cost": [],
                    "quantity": [],
                    "total_gl": [],
                    "percent_total_gl": [],
                },
            ):
                parsed = extractor.tesseract_row_stream_fields(
                    image=mock.Mock(),
                    geometry=extractor.RowGeometry(0, 10, 0.2, []),
                    column_ranges=extractor.DEFAULT_COLUMN_RANGES,
                    section_cache={},
                )

        self.assertEqual(parsed["change"], "-$0.26")

    def test_tesseract_row_stream_fields_prefers_sequential_bid_ask_when_schema_prices_are_implausible(self) -> None:
        items = [
            extractor.OcrItem(text="$13.00", x=0.20, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+$1.95", x=0.28, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+64%", x=0.34, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$12.75", x=0.42, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="$13.00", x=0.48, y=0.0, width=0.01, height=0.01),
        ]

        with mock.patch("extract.row_section_ocr_items", return_value=items):
            with mock.patch(
                "extract.collect_schema_field_texts",
                return_value={
                    "last": [],
                    "change": [],
                    "percent_change": [],
                    "bid": ["+17"],
                    "ask": ["$12.75"],
                    "volume": [],
                    "day_range_low": [],
                    "day_range_high": [],
                    "week_52_low": [],
                    "week_52_high": [],
                    "avg_cost": [],
                    "quantity": [],
                    "total_gl": [],
                    "percent_total_gl": [],
                },
            ):
                parsed = extractor.tesseract_row_stream_fields(
                    image=mock.Mock(),
                    geometry=extractor.RowGeometry(0, 10, 0.2, []),
                    column_ranges=extractor.DEFAULT_COLUMN_RANGES,
                    section_cache={},
                )

        self.assertEqual(parsed["bid"], "$12.75")
        self.assertEqual(parsed["ask"], "$13.00")

    def test_repair_percent_change_from_price_fields_reduces_shifted_percent(self) -> None:
        repaired = extractor.repair_percent_change_from_price_fields(
            {
                "last": "$411.22",
                "change": "+$18.11",
                "percent_change": "44.61%",
            }
        )
        self.assertEqual(repaired["percent_change"], "+4.61%")

    def test_repair_percent_change_from_price_fields_restores_negative_sign(self) -> None:
        repaired = extractor.repair_percent_change_from_price_fields(
            {
                "last": "$0.18",
                "change": "-$0.16",
                "percent_change": "+64.93%",
            }
        )
        self.assertEqual(repaired["percent_change"], "-47.06%")

    def test_repair_volume_quantity_swap_moves_large_integer_to_volume(self) -> None:
        repaired = extractor.repair_volume_quantity_swap(
            {
                "volume": "2",
                "quantity": "3,188,082",
            }
        )
        self.assertEqual(repaired["volume"], "3,188,082")
        self.assertEqual(repaired["quantity"], "2")

    def test_repair_position_pnl_fields_adds_sign_and_percent(self) -> None:
        repaired = extractor.repair_position_pnl_fields(
            {
                "last": "$65.36",
                "avg_cost": "$76.90",
                "quantity": "325",
                "total_gl": "$3751.84",
                "percent_total_gl": "01%",
            }
        )
        self.assertEqual(repaired["total_gl"], "-$3751.84")
        self.assertEqual(repaired["percent_total_gl"], "-15.01%")

    def test_repair_position_pnl_fields_fills_missing_total_gl_from_position_math(self) -> None:
        repaired = extractor.repair_position_pnl_fields(
            {
                "instrument_type": "option",
                "last": "$3.00",
                "avg_cost": "$1.92",
                "quantity": "-52",
                "total_gl": "",
                "percent_total_gl": "",
            }
        )
        self.assertEqual(repaired["total_gl"], "-$5616.00")
        self.assertEqual(repaired["percent_total_gl"], "-56.25%")

    def test_adopt_raw_quantity_sign_prefers_plausible_equity_quantity(self) -> None:
        repaired = extractor.adopt_raw_quantity_sign(
            {
                "instrument_type": "equity",
                "quantity": "2",
            },
            {"quantity": "325"},
        )
        self.assertEqual(repaired["quantity"], "325")

    def test_adopt_raw_quantity_sign_prefers_nearby_negative_option_quantity(self) -> None:
        repaired = extractor.adopt_raw_quantity_sign(
            {
                "instrument_type": "option",
                "quantity": "-20",
            },
            {"quantity": "-18"},
        )
        self.assertEqual(repaired["quantity"], "-18")

    def test_repair_quantity_from_position_uses_total_gl_math(self) -> None:
        repaired = extractor.repair_quantity_from_position(
            {
                "instrument_type": "equity",
                "last": "$65.36",
                "avg_cost": "$76.90",
                "total_gl": "-$3751.84",
                "quantity": "2",
            }
        )
        self.assertEqual(repaired["quantity"], "325")

    def test_repair_option_quantity_from_position_uses_contract_multiplier(self) -> None:
        repaired = extractor.repair_quantity_from_position(
            {
                "instrument_type": "option",
                "last": "$3.00",
                "avg_cost": "$1.92",
                "total_gl": "-$5630.99",
                "quantity": "52",
            }
        )
        self.assertEqual(repaired["quantity"], "-52")

    def test_repair_quantity_sign_from_position_sets_negative_option_sign(self) -> None:
        repaired = extractor.repair_quantity_sign_from_position(
            {
                "instrument_type": "option",
                "last": "$4.30",
                "avg_cost": "$4.35",
                "total_gl": "+$98.89",
                "quantity": "18",
            }
        )
        self.assertEqual(repaired["quantity"], "-18")


class ExtractorContractTests(unittest.TestCase):
    def test_parse_main_row_normalizes_sample_like_numeric_noise(self) -> None:
        row = [
            extractor.OcrItem(text="+599%", x=0.355, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+179 94%", x=0.961, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="+$372,554.44", x=0.881, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="73.79", x=0.580, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="77.93", x=0.612, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="68л6", x=0.640, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="101.99", x=0.672, y=0.0, width=0.01, height=0.01),
        ]

        parsed = extractor.parse_main_row(row)

        self.assertEqual(parsed["percent_change"], "+5.99%")
        self.assertEqual(parsed["percent_total_gl"], "+179.94%")
        self.assertEqual(parsed["day_range_low"], "73.79")
        self.assertEqual(parsed["day_range_high"], "77.93")
        self.assertEqual(parsed["week_52_low"], "68.46")
        self.assertEqual(parsed["week_52_high"], "101.99")

    def test_parse_main_row_repairs_range_decimal_loss(self) -> None:
        row = [
            extractor.OcrItem(text="+762%", x=0.355, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="-1,90%", x=0.969, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="-$2 281.50", x=0.890, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="362.50", x=0.580, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="394.65", x=0.610, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="222 79", x=0.641, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="498.83", x=0.672, y=0.0, width=0.01, height=0.01),
        ]

        parsed = extractor.parse_main_row(row)

        self.assertEqual(parsed["percent_change"], "+7.62%")
        self.assertEqual(parsed["percent_total_gl"], "-1.90%")
        self.assertEqual(parsed["week_52_low"], "222.79")
        self.assertEqual(parsed["week_52_high"], "498.83")

    def test_parse_main_row_uses_header_derived_ranges_for_resized_layout(self) -> None:
        header_row = [
            extractor.OcrItem(text="Symbol", x=resize_x(0.04), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Last", x=resize_x(0.22), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Change", x=resize_x(0.29), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="%", x=resize_x(0.35), y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Change", x=resize_x(0.36), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Bid", x=resize_x(0.42), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Ask", x=resize_x(0.48), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Volume", x=resize_x(0.53), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Day", x=resize_x(0.585), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="range", x=resize_x(0.612), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="52-week", x=resize_x(0.655), y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="range", x=resize_x(0.695), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Avg Cost", x=resize_x(0.725), y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="Quantity", x=resize_x(0.805), y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="$", x=resize_x(0.86), y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Total", x=resize_x(0.875), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="G/L", x=resize_x(0.915), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="%", x=resize_x(0.95), y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Total", x=resize_x(0.962), y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="G/L", x=resize_x(0.995), y=0.0, width=0.03, height=0.01),
        ]
        column_ranges = extractor.derive_column_ranges(header_row)

        def cell_x(field_name: str, width: float = 0.02) -> float:
            left, right = column_ranges[field_name]
            return ((left + right) / 2) - (width / 2)

        row = [
            extractor.OcrItem(text="$76.72", x=cell_x("last"), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="+$4.33", x=cell_x("change", 0.03), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="+599%", x=cell_x("percent_change"), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="$76.50", x=cell_x("bid"), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="$76.90", x=cell_x("ask"), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="12,345", x=cell_x("volume", 0.03), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="73.79", x=cell_x("day_range_low"), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="77.93", x=cell_x("day_range_high"), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="6846", x=cell_x("week_52_low"), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="101.99", x=cell_x("week_52_high", 0.03), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="$70.01", x=cell_x("avg_cost", 0.03), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="100", x=cell_x("quantity"), y=0.0, width=0.02, height=0.01),
        ]

        parsed = extractor.parse_main_row(row, column_ranges)

        self.assertEqual(parsed["last"], "$76.72")
        self.assertEqual(parsed["change"], "+$4.33")
        self.assertEqual(parsed["percent_change"], "+5.99%")
        self.assertEqual(parsed["bid"], "$76.50")
        self.assertEqual(parsed["ask"], "$76.90")
        self.assertEqual(parsed["volume"], "12,345")
        self.assertEqual(parsed["quantity"], "100")
        self.assertEqual(parsed["day_range_low"], "73.79")
        self.assertEqual(parsed["day_range_high"], "77.93")
        self.assertEqual(parsed["week_52_low"], "68.46")
        self.assertEqual(parsed["week_52_high"], "101.99")

    def test_extract_header_anchors_accepts_exact_multi_token_headers(self) -> None:
        header_row = [
            extractor.OcrItem(text="Symbol", x=0.04, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Last", x=0.22, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Change", x=0.29, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="%", x=0.35, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Change", x=0.36, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Bid", x=0.42, y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Ask", x=0.48, y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Volume", x=0.53, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Day", x=0.585, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="range", x=0.612, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="52-week", x=0.655, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="range", x=0.695, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Avg.", x=0.725, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="cost", x=0.755, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Quantity", x=0.805, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="$", x=0.86, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Total", x=0.875, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="G/L", x=0.915, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="%", x=0.95, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Total", x=0.955, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="G/L", x=0.982, y=0.0, width=0.015, height=0.01),
        ]

        anchors = extractor.extract_header_anchors(header_row)

        for key in ("symbol", "total_gl", "percent_total_gl"):
            self.assertIn(key, anchors)

    def test_extract_header_anchors_backfills_noisy_tesseract_header_positions(self) -> None:
        header_row = [
            extractor.OcrItem(text="symbol", x=0.01, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Last", x=0.23, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Change", x=0.29, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="%", x=0.35, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Change", x=0.36, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Bid", x=0.42, y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Ask", x=0.48, y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Volume", x=0.54, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Dayrange", x=0.60, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="_S2.week", x=0.67, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="range", x=0.71, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Aug.", x=0.75, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="cost", x=0.78, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Quantity", x=0.83, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="STotal", x=0.90, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Git", x=0.94, y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Total", x=0.97, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="G/L", x=0.995, y=0.0, width=0.02, height=0.01),
        ]

        anchors = extractor.extract_header_anchors(header_row)

        self.assertEqual(set(anchors), set(extractor.required_header_keys()))

    def test_is_header_row_rejects_ocr_miss_on_required_headers(self) -> None:
        header_row = [
            extractor.OcrItem(text="Symbol", x=0.04, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Last", x=0.22, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Change", x=0.29, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="%", x=0.35, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Change", x=0.36, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Bid", x=0.42, y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Acl", x=0.48, y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Volume", x=0.53, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Quantity", x=0.805, y=0.0, width=0.05, height=0.01),
        ]

        self.assertFalse(extractor.is_header_row(header_row))

    def test_is_header_row_requires_all_monitoring_headers(self) -> None:
        header_row = [
            extractor.OcrItem(text="Symbol", x=0.04, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Last", x=0.22, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Change", x=0.29, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="%", x=0.35, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Change", x=0.36, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Bid", x=0.42, y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Ask", x=0.48, y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="Volume", x=0.53, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Day", x=0.585, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="range", x=0.612, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="52-week", x=0.655, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="range", x=0.695, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Avg.", x=0.725, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="cost", x=0.755, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Quantity", x=0.805, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="$", x=0.86, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Total", x=0.875, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="G/L", x=0.915, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="%", x=0.95, y=0.0, width=0.01, height=0.01),
            extractor.OcrItem(text="Total", x=0.962, y=0.0, width=0.04, height=0.01),
        ]

        self.assertFalse(extractor.is_header_row(header_row))

    def test_validate_image_quality_rejects_too_small_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "small.png"
            from PIL import Image

            Image.new("RGB", (400, 300), color="white").save(image_path)
            with self.assertRaisesRegex(extractor.ImageQualityError, "too small"):
                extractor.validate_image_quality(image_path)

    def test_validate_required_fields_raises_for_missing_monitoring_columns(self) -> None:
        record = {
            "symbol": "UBER",
            "last": "$76.72",
            "change": "+$4.33",
            "percent_change": "+5.99%",
            "bid": "$76.50",
            "ask": "$76.90",
            "volume": "",
            "quantity": "",
            "day_range_low": "",
            "day_range_high": "",
            "week_52_low": "68.46",
            "week_52_high": "101.99",
        }

        with self.assertRaisesRegex(ValueError, "quantity"):
            extractor.validate_required_fields(record, Path("fixture_input.png"))

    def test_validate_required_fields_allows_optional_monitoring_columns_to_be_blank(self) -> None:
        record = {
            "symbol": "UBER",
            "last": "$76.72",
            "change": "+$4.33",
            "percent_change": "+5.99%",
            "bid": "$76.50",
            "ask": "$76.90",
            "volume": "12,345",
            "quantity": "100",
            "day_range_low": "",
            "day_range_high": "",
            "week_52_low": "",
            "week_52_high": "",
            "avg_cost": "",
            "total_gl": "",
            "percent_total_gl": "",
        }

        extractor.validate_required_fields(record, Path("fixture_input.png"))

    def test_validate_field_shapes_rejects_malformed_option_expiration(self) -> None:
        with self.assertRaisesRegex(ValueError, "expiration"):
            extractor.validate_field_shapes(
                {
                    "symbol": "GOOGL 325 Put",
                    "instrument_type": "option",
                    "expiration": "G) — PAL GOOGL Apr TRPADL 17 20264 325 I TINA. Put VPA OE LL",
                    "last": "$1.23",
                    "change": "+$0.10",
                    "percent_change": "+8.85%",
                    "bid": "$1.22",
                    "ask": "$1.24",
                    "quantity": "-1",
                },
                Path("fixture_input.png"),
            )

    def test_validate_field_shapes_rejects_invalid_instrument_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "instrument_type"):
            extractor.validate_field_shapes(
                {
                    "symbol": "GOOGL",
                    "instrument_type": "unknown",
                    "expiration": "",
                    "last": "$174.10",
                    "change": "+$1.25",
                    "percent_change": "+0.72%",
                    "bid": "$174.00",
                    "ask": "$174.15",
                    "quantity": "10",
                },
                Path("fixture_input.png"),
            )

    def test_validate_cross_field_consistency_rejects_last_outside_day_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "Last is outside day range"):
            extractor.validate_cross_field_consistency(
                {
                    "symbol": "GOOGL 340 Call",
                    "last": "$21.55",
                    "day_range_low": "27.49",
                    "day_range_high": "29.30",
                },
                Path("fixture_input.png"),
            )

    def test_validate_cross_field_consistency_rejects_change_sign_conflict(self) -> None:
        with self.assertRaisesRegex(ValueError, "Change sign conflicts"):
            extractor.validate_cross_field_consistency(
                {
                    "symbol": "FDIG",
                    "change": "$4.25",
                    "percent_change": "-3.1%",
                },
                Path("fixture_input.png"),
            )

    def test_timestamp_only_output_filename(self) -> None:
        file_name = extractor.csv_name(sample_created_at())
        self.assertTrue(file_name.startswith("positions_monitoring_"))
        self.assertTrue(file_name.endswith(".csv"))
        self.assertEqual(file_name.count(".csv"), 1)

    def test_process_image_raises_on_timestamp_collision_for_different_png(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fixture_dir = temp_path / "existing_csv_fixture"
            fixture_dir.mkdir()
            csv_path = fixture_dir / extractor.csv_name(sample_created_at())
            csv_path.write_text(
                "schema_name,image_file,created_at,symbol,instrument_type,description,expiration,last,change,percent_change,bid,ask,volume,day_range_low,day_range_high,week_52_low,week_52_high,avg_cost,quantity,total_gl,percent_total_gl\n"
                "monitoring,fixture_existing.png,2030-01-02T03:04:05-08:00,FBTC,equity,,,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n",
                encoding="utf-8",
            )
            image_path = temp_path / "fixture_input.png"
            image_path.write_bytes(b"not-a-real-png")

            original_output_dir = extractor.OUTPUT_DIR
            original_image_created_at = extractor.image_created_at
            extractor.OUTPUT_DIR = fixture_dir
            extractor.image_created_at = lambda _: sample_created_at()
            try:
                with self.assertRaises(FileExistsError):
                    extractor.process_image(image_path)
            finally:
                extractor.OUTPUT_DIR = original_output_dir
                extractor.image_created_at = original_image_created_at

    def test_main_continues_after_per_file_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            good_png = input_dir / "good.png"
            bad_png = input_dir / "bad.png"
            good_png.write_bytes(b"good")
            bad_png.write_bytes(b"bad")

            def fake_process_image(image_path: Path) -> tuple[str, Path]:
                if image_path.name == "bad.png":
                    raise extractor.ImageQualityError("missing monitoring headers")
                return "extracted", output_dir / "positions_monitoring_fixture.csv"

            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            with mock.patch("extract.process_image", side_effect=fake_process_image):
                with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                    exit_code = extractor.main(
                        ["--input-dir", str(input_dir), "--output-dir", str(output_dir)]
                    )

            self.assertEqual(exit_code, 1)
            self.assertIn("extracted good.png -> positions_monitoring_fixture.csv", stdout_buffer.getvalue())
            self.assertIn("processed 2 input file(s): 1 extracted, 0 skipped, 1 failed", stdout_buffer.getvalue())
            self.assertIn("failed    bad.png -> missing monitoring headers", stderr_buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
