from pathlib import Path
import tempfile
import unittest

import extract as extractor


class ExtractorHelperTests(unittest.TestCase):
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

    def test_percent_normalization_repairs_missing_decimal_and_sign(self) -> None:
        self.assertEqual(extractor.normalize_percent_text("+599%"), "+5.99%")
        self.assertEqual(extractor.normalize_percent_text("+179 94%"), "+179.94%")
        self.assertEqual(extractor.normalize_percent_text("-1,90%"), "-1.90%")
        self.assertEqual(
            extractor.normalize_percent_text("22.12%", paired_amount="-$20,189.95"),
            "-22.12%",
        )

    def test_repair_record_from_crop_texts_updates_symbol_and_missing_fields(self) -> None:
        record = {
            "schema_name": "monitoring",
            "image_file": "sample.png",
            "created_at": "2026-04-15T14:31:16-07:00",
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
        self.assertEqual(repaired["quantity"], "-18")
        self.assertEqual(repaired["change"], "+$1.96")
        self.assertEqual(repaired["day_range_low"], "2.62")
        self.assertEqual(repaired["week_52_high"], "28.00")


class SampleImageExtractionTests(unittest.TestCase):
    SAMPLE_IMAGE = Path("input/Screenshot 2026-04-15 at 14.31.11.png")

    @unittest.skipUnless(SAMPLE_IMAGE.exists(), "sample screenshot not available locally")
    def test_sample_image_repairs_missing_quantities_and_bottom_row_identity(self) -> None:
        records = extractor.build_records(self.SAMPLE_IMAGE)
        by_key = {(row["symbol"], row["expiration"]): row for row in records}

        self.assertEqual(by_key[("FBTC 70 Call", "May 15 2026")]["quantity"], "-3")
        self.assertEqual(by_key[("FDIG 40 Call", "May 15 2026")]["quantity"], "-6")
        self.assertEqual(by_key[("GOOGL 325 Put", "Apr 17 2026")]["quantity"], "-5")
        self.assertEqual(by_key[("NVDA 195 Call", "Jul 17 2026")]["quantity"], "-4")
        self.assertEqual(by_key[("TSLA 360 Put", "Aug 21 2026")]["quantity"], "-2")
        self.assertEqual(by_key[("UBER 80 Call", "Jun 18 2026")]["quantity"], "-18")
        self.assertTrue(
            all(row["quantity"] for row in records if row["instrument_type"] == "option")
        )

        self.assertEqual(by_key[("MSFT 420 Call", "Aug 21 2026")]["percent_change"], "+0.44%")
        self.assertEqual(by_key[("PLTR 150 Call", "Jun 18 2026")]["day_range_high"], "10.31")
        self.assertEqual(by_key[("PLTR 150 Call", "Jun 18 2026")]["week_52_low"], "4.40")
        self.assertEqual(by_key[("UBER", "")]["percent_change"], "+5.99%")
        self.assertEqual(by_key[("UBER", "")]["percent_total_gl"], "+179.94%")
        self.assertEqual(by_key[("TSLA", "")]["percent_change"], "+7.62%")
        self.assertEqual(by_key[("TSLA", "")]["percent_total_gl"], "-1.90%")
        self.assertEqual(by_key[("PLTR", "")]["percent_total_gl"], "-22.12%")

    def test_timestamp_only_output_filename(self) -> None:
        created_at = extractor.datetime.fromisoformat("2026-04-15T14:31:16-07:00")
        self.assertEqual(
            extractor.csv_name(created_at),
            "positions_monitoring_20260415T143116-0700.csv",
        )

    def test_process_image_raises_on_timestamp_collision_for_different_png(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "output"
            output_dir.mkdir()
            csv_path = output_dir / "positions_monitoring_20260415T143116-0700.csv"
            csv_path.write_text(
                "schema_name,image_file,created_at,symbol,instrument_type,description,expiration,last,change,percent_change,bid,ask,volume,day_range_low,day_range_high,week_52_low,week_52_high,avg_cost,quantity,total_gl,percent_total_gl\n"
                "monitoring,other.png,2026-04-15T14:31:16-07:00,FBTC,equity,,,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n",
                encoding="utf-8",
            )
            image_path = temp_path / "sample.png"
            image_path.write_bytes(b"not-a-real-png")

            original_output_dir = extractor.OUTPUT_DIR
            original_image_created_at = extractor.image_created_at
            extractor.OUTPUT_DIR = output_dir
            extractor.image_created_at = lambda _: extractor.datetime.fromisoformat(
                "2026-04-15T14:31:16-07:00"
            )
            try:
                with self.assertRaises(FileExistsError):
                    extractor.process_image(image_path)
            finally:
                extractor.OUTPUT_DIR = original_output_dir
                extractor.image_created_at = original_image_created_at


if __name__ == "__main__":
    unittest.main()
