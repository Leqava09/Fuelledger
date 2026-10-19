from datetime import date, datetime
from decimal import Decimal

from django.test import SimpleTestCase

from ledger.report_parser import ReportParseError, parse_report


class ReportParserTests(SimpleTestCase):
	def test_parses_report_period_and_nozzle_rows(self):
		text = """Business Analysis Report
From: 2026-10-06 00:00:00                      To:2026-10-06 23:59:59
Sales Report from Pumps (Electronic totalizer movement by nozzle)
  1    DIESEL50PPM        7040.00       7667.00                        627.00              626.58                    -.42
   3     ULP93                  834621.00          834796.00            175.00                175.36                  .36
Fuel Sales Summary
DIESEL50PPM                           2166.440            68428.80                    .00                   71                  963.79
ULP93                                  837.790            22487.80                    .00                   52                  432.46
Payment and Bankable cash analysis summary
Sales Report: calculated from Purchases and Dips
Opening Values
Grade                   Dip Level     Dip Date              Variance   Calculated Volume
DIESEL50PPM              7127.55 @ 2026-10-06 04:55:00        -40.24            7127.55 @ 2026-10-06 00:00:00
ULP93                    1235.25 @ 2026-10-06 04:55:00         64.75            1235.25 @ 2026-10-06 00:00:00
Closing Values
Grade                   Dip Level     Dip Date              Variance   Calculated Volume
DIESEL50PPM              7127.55 @ 2026-10-06 04:55:00        -40.24            9293.99 @ 2026-10-06 23:59:59
ULP93                    1235.25 @ 2026-10-06 04:55:00         64.75            2073.04 @ 2026-10-06 23:59:59
"""

		parsed = parse_report(text)

		self.assertEqual(parsed["business_date"], date(2026, 10, 6))
		self.assertEqual(parsed["period_from"], datetime(2026, 10, 6, 0, 0))
		self.assertEqual(parsed["period_to"], datetime(2026, 10, 6, 23, 59, 59))
		self.assertEqual(parsed["nozzles"][0]["opening"], Decimal("7040.00"))
		self.assertEqual(parsed["nozzles"][0]["closing"], Decimal("7667.00"))
		self.assertEqual(parsed["nozzles"][0]["variance"], Decimal("-0.42"))
		self.assertEqual(parsed["nozzles"][1]["number"], 3)
		self.assertEqual(parsed["fuel_sales"][0]["grade"], "DIESEL50PPM")
		self.assertEqual(parsed["fuel_sales"][0]["litres"], Decimal("2166.440"))
		self.assertEqual(parsed["tank_readings"][0]["opening"], Decimal("7127.55"))
		self.assertEqual(parsed["tank_readings"][0]["closing"], Decimal("9293.99"))
		self.assertEqual(parsed["tank_readings"][1]["grade_index"], 1)

	def test_rejects_non_reports_and_reports_without_nozzle_rows(self):
		with self.assertRaises(ReportParseError):
			parse_report("A different document")
		with self.assertRaises(ReportParseError):
			parse_report("Business Analysis Report\nFrom: 2026-10-06 00:00:00 To:2026-10-06 23:59:59")