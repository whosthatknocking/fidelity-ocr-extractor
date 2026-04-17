import json
from pathlib import Path
import tempfile
import unittest

import extract as extractor


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

    def test_monitoring_contract_loads_required_fields_from_toml(self) -> None:
        self.assertEqual(
            extractor.required_fields(),
            ["symbol", "last", "change", "percent_change", "bid", "ask", "volume", "quantity"],
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

    def test_money_and_integer_normalizers_repair_ocr_noise(self) -> None:
        self.assertEqual(extractor.normalize_money_text("S0K6"), "$0.06")
        self.assertEqual(extractor.normalize_money_text("-$2 281.50"), "-$2281.50")
        self.assertEqual(extractor.normalize_integer_text("3.191 887"), "3,191,887")
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

    def test_parse_symbol_block_ignores_stale_expiration_prefix(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["May 15 2026", "FDIG", "FIDELITY CRYPTO INDUSTRY AND DIGITAL PAYMENTS ETF"]
        )
        self.assertEqual(symbol, "FDIG")
        self.assertEqual(instrument_type, "equity")
        self.assertEqual(
            description,
            extractor.normalize_description(
                "FIDELITY CRYPTO INDUSTRY AND DIGITAL PAYMENTS ETF"
            ),
        )
        self.assertEqual(expiration, "")

    def test_parse_symbol_block_prefers_latest_option_candidate(self) -> None:
        symbol, instrument_type, description, expiration = extractor.parse_symbol_block(
            ["GOOGL 325 Put", "GOOGL 315 Put", "GOOGL 340 Call", "Aug 21 2026"]
        )
        self.assertEqual(symbol, "GOOGL 340 Call")
        self.assertEqual(instrument_type, "option")
        self.assertEqual(expiration, "Aug 21 2026")

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

        with self.assertRaisesRegex(ValueError, "volume, quantity"):
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


if __name__ == "__main__":
    unittest.main()
