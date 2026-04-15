import csv
import tempfile
import unittest
from pathlib import Path

import viewer


class ViewerHelperTests(unittest.TestCase):
    def test_format_timestamp_from_name_uses_timestamp_only_pattern(self) -> None:
        self.assertEqual(
            viewer.format_timestamp_from_name("positions_monitoring_20260415T143116-0700.csv"),
            "2026-04-15T14:31:16-07:00",
        )

    def test_build_data_payload_uses_created_at_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            csv_path = output_dir / "positions_monitoring_20260415T143116-0700.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
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
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "schema_name": "monitoring",
                        "image_file": "sample.png",
                        "created_at": "2026-04-15T14:31:16-07:00",
                        "symbol": "FBTC",
                        "instrument_type": "equity",
                        "description": "FUND",
                        "expiration": "",
                        "last": "$1.00",
                        "change": "+$0.01",
                        "percent_change": "+1.00%",
                        "bid": "$1.00",
                        "ask": "$1.01",
                        "volume": "100",
                        "day_range_low": "0.90",
                        "day_range_high": "1.10",
                        "week_52_low": "0.50",
                        "week_52_high": "2.00",
                        "avg_cost": "$0.80",
                        "quantity": "10",
                        "total_gl": "+$2.00",
                        "percent_total_gl": "+20.00%",
                    }
                )

            original_output_dir = viewer.OUTPUT_DIR
            viewer.OUTPUT_DIR = output_dir
            try:
                payload = viewer.build_data_payload(csv_path.name)
            finally:
                viewer.OUTPUT_DIR = original_output_dir

            self.assertEqual(
                payload["freshness_summary"]["source_created_at"],
                "2026-04-15T14:31:16-07:00",
            )
            self.assertEqual(payload["display_name"], "2026-04-15T14:31:16-07:00")


if __name__ == "__main__":
    unittest.main()
