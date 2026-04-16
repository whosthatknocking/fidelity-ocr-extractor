from pathlib import Path
import tempfile
import unittest

import extract as extractor


def sample_created_at() -> extractor.datetime:
    return extractor.datetime.fromisoformat("2030-01-02T03:04:05-08:00")


def resize_x(x_value: float) -> float:
    return (0.82 * x_value) + 0.06


class ExtractorHelperTests(unittest.TestCase):
    def test_header_aliases_match_degraded_labels(self) -> None:
        self.assertTrue(extractor.header_matches("SymDol", "symbol"))
        self.assertTrue(extractor.header_matches("Chang", "change"))
        self.assertTrue(extractor.header_matches("Act", "bid"))
        self.assertTrue(extractor.header_matches("AVS. COS", "avg_cost"))

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
            extractor.OcrItem(text="52-week", x=resize_x(0.655), y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="Avg Cost", x=resize_x(0.725), y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="Quantity", x=resize_x(0.805), y=0.0, width=0.05, height=0.01),
        ]
        column_ranges = extractor.derive_column_ranges(header_row)
        row = [
            extractor.OcrItem(text="$76.72", x=resize_x(0.225), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="+$4.33", x=resize_x(0.295), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="+599%", x=resize_x(0.355), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="$76.50", x=resize_x(0.425), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="$76.90", x=resize_x(0.485), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="12,345", x=resize_x(0.530), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="73.79", x=resize_x(0.580), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="77.93", x=resize_x(0.612), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="6846", x=resize_x(0.640), y=0.0, width=0.02, height=0.01),
            extractor.OcrItem(text="101.99", x=resize_x(0.672), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="$70.01", x=resize_x(0.735), y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="100", x=resize_x(0.805), y=0.0, width=0.02, height=0.01),
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

    def test_derive_column_ranges_uses_degraded_header_aliases(self) -> None:
        header_row = [
            extractor.OcrItem(text="SymDol", x=0.00, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="Last", x=0.23, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Chang", x=0.30, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="% Change", x=0.36, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="Act", x=0.43, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Ask", x=0.49, y=0.0, width=0.03, height=0.01),
            extractor.OcrItem(text="Volume", x=0.56, y=0.0, width=0.04, height=0.01),
            extractor.OcrItem(text="Day range", x=0.61, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="AVS. COS", x=0.73, y=0.0, width=0.05, height=0.01),
            extractor.OcrItem(text="Quantity", x=0.81, y=0.0, width=0.05, height=0.01),
        ]

        column_ranges = extractor.derive_column_ranges(header_row)

        self.assertLess(column_ranges["bid"][0], 0.45)
        self.assertGreater(column_ranges["bid"][1], column_ranges["bid"][0])
        self.assertLess(column_ranges["avg_cost"][0], 0.77)

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
            "day_range_low": "73.79",
            "day_range_high": "77.93",
            "week_52_low": "68.46",
            "week_52_high": "101.99",
        }

        with self.assertRaisesRegex(ValueError, "volume, quantity"):
            extractor.validate_required_fields(record, Path("fixture_input.png"))

    def test_validate_required_fields_allows_missing_52_week_range(self) -> None:
        record = {
            "symbol": "UBER",
            "last": "$76.72",
            "change": "+$4.33",
            "percent_change": "+5.99%",
            "bid": "$76.50",
            "ask": "$76.90",
            "volume": "12,345",
            "quantity": "100",
            "day_range_low": "73.79",
            "day_range_high": "77.93",
            "week_52_low": "",
            "week_52_high": "",
        }

        extractor.validate_required_fields(record, Path("fixture_input.png"))

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
