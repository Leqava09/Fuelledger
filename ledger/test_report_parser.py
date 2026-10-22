from datetime import date, datetime
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from ledger.report_parser import ReportParseError, extract_text, parse_report


class ReportParserTests(SimpleTestCase):
	REPORT_TEXT = """Business Analysis Report
From: 2026-10-06 00:00:00                      To:2026-10-06 23:59:59
Transactions summary as per POS system
Dry sales: 784.000 10482.96 1169.38 11652.34 2687.10 14.86
Fuel sales:                             4083.530           119973.10                    .00          119973.10          119973.10             593.93
Sales Report from Pumps (Electronic totalizer movement by nozzle)
  1    DIESEL50PPM        7040.00       7667.00                        627.00              626.58                    -.42
   3     ULP93                  834621.00          834796.00            175.00                175.36                  .36
Totals:          802.00              801.94                  -.06
Sales Report from Pumps (Machinical totalizer movement by nozzle)
Nozzle Grade Opening Volume Closing Volume Totalizer in L Fuel Sales Variance
   1 DIESEL50PPM 7040.00 7667.00 627.00 626.58 -.42
Fuel Sales Summary
DIESEL50PPM                            626.580             19769.60                    .00                   71                    8.82
ULP93                                  175.360              4700.00                    .00                   52                    3.37
Totals:           801.940             24469.60                    .00               123.00
Payment and Bankable cash analysis summary
Total Account:                2162.20                    Total Cash:                 29564.84              Money Drops:                25130.00
Total Fleet Card: .00
Total Debit Card: .00
Total Credit Card:             18250.35
TotalCheque: 60.50
Total Payouts:                 2142.45
Money Drops for Period:                  25130.00
Total Left in Drawer: 2292.39
Fuel Sales by Grade
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

	def test_parses_report_period_and_nozzle_rows(self):
		text = self.REPORT_TEXT
		parsed = parse_report(text)

		self.assertEqual(parsed["business_date"], date(2026, 10, 6))
		self.assertEqual(parsed["period_from"], datetime(2026, 10, 6, 0, 0))
		self.assertEqual(parsed["period_to"], datetime(2026, 10, 6, 23, 59, 59))
		self.assertEqual(parsed["nozzles"][0]["opening"], Decimal("7040.00"))
		self.assertEqual(parsed["nozzles"][0]["closing"], Decimal("7667.00"))
		self.assertEqual(parsed["nozzles"][0]["variance"], Decimal("-0.42"))
		self.assertEqual(parsed["nozzles"][1]["number"], 3)
		self.assertEqual(parsed["fuel_sales"][0]["grade"], "DIESEL50PPM")
		self.assertEqual(parsed["fuel_sales"][0]["litres"], Decimal("626.580"))
		self.assertEqual(parsed["tank_readings"][0]["opening"], Decimal("7127.55"))
		self.assertEqual(parsed["tank_readings"][0]["closing"], Decimal("9293.99"))
		self.assertEqual(parsed["tank_readings"][1]["grade_index"], 1)
		self.assertEqual(parsed["cash_up"]["forecourt_total"], Decimal("119973.10"))
		self.assertEqual(parsed["cash_up"]["safedrops"], Decimal("25130.00"))
		self.assertEqual(parsed["cash_up"]["account_1"], Decimal("2162.20"))
		self.assertEqual(parsed["cash_up"]["payout_1"], Decimal("2142.45"))
		self.assertEqual(parsed["cash_up"]["credit_card_total"], Decimal("18250.35"))
		self.assertEqual(parsed["cash_up"]["fleet_card_total"], Decimal("0.00"))
		self.assertEqual(parsed["cash_up"]["debit_card_total"], Decimal("0.00"))
		self.assertEqual(parsed["cash_up"]["shop_total"], Decimal("11652.34"))
		self.assertEqual(parsed["cash_up"]["cash_float"], Decimal("2292.39"))
		self.assertEqual(parsed["cash_up"]["voucher_1"], Decimal("60.50"))
		self.assertEqual(len(parsed["mechanical_nozzles"]), 1)
		self.assertEqual(parsed["mechanical_nozzles"][0]["totalizer_litres"], Decimal("627.00"))
		self.assertEqual(parsed["warnings"], [])

	def test_rejects_unrecognized_numbered_nozzle_rows(self):
		text = self.REPORT_TEXT.replace("DIESEL50PPM        7040.00", "PETROL95           7040.00")
		with self.assertRaisesRegex(ReportParseError, "numbered electronic nozzle rows"):
			parse_report(text)

	def test_reports_totalizer_movement_mismatch_as_warning(self):
		text = self.REPORT_TEXT.replace("7667.00", "7668.00", 1)

		parsed = parse_report(text)

		self.assertTrue(any("Nozzle 1" in warning for warning in parsed["warnings"]))

	def test_reports_pump_vs_pos_sales_mismatch_as_warning(self):
		text = self.REPORT_TEXT.replace("626.580", "625.580", 1).replace("801.940", "800.940", 1)

		parsed = parse_report(text)

		self.assertTrue(any("Pump fuel sales" in warning for warning in parsed["warnings"]))

	def test_credit_card_total_is_optional_when_report_omits_it(self):
		text = self.REPORT_TEXT.replace("Total Credit Card:             18250.35\n", "")

		parsed = parse_report(text)

		self.assertIsNone(parsed["cash_up"]["credit_card_total"])

	def test_fleet_and_debit_card_totals_are_optional(self):
		text = self.REPORT_TEXT.replace("Total Fleet Card: .00\n", "").replace("Total Debit Card: .00\n", "")

		parsed = parse_report(text)

		self.assertIsNone(parsed["cash_up"]["fleet_card_total"])
		self.assertIsNone(parsed["cash_up"]["debit_card_total"])

	def test_mechanical_readings_are_optional(self):
		text = self.REPORT_TEXT.replace(
			"Sales Report from Pumps (Machinical totalizer movement by nozzle)\n"
			"Nozzle Grade Opening Volume Closing Volume Totalizer in L Fuel Sales Variance\n"
			"   1 DIESEL50PPM 7040.00 7667.00 627.00 626.58 -.42\n",
			"",
		)

		parsed = parse_report(text)

		self.assertEqual(parsed["mechanical_nozzles"], [])

	def test_enforces_pdf_page_limit(self):
		pdfplumber = ModuleType("pdfplumber")
		pdfplumber.open = MagicMock()
		pdfplumber.open.return_value.__enter__.return_value.pages = [SimpleNamespace() for _ in range(6)]
		with patch.dict("sys.modules", {"pdfplumber": pdfplumber}):
			with self.assertRaisesRegex(ReportParseError, "no more than 5 pages"):
				extract_text(SimpleNamespace())

	def test_rejects_non_reports_and_reports_without_nozzle_rows(self):
		with self.assertRaises(ReportParseError):
			parse_report("A different document")
		with self.assertRaises(ReportParseError):
			parse_report("Business Analysis Report\nFrom: 2026-10-06 00:00:00 To:2026-10-06 23:59:59")