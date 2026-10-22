"""Parser for fuel readings in POS Business Analysis Reports."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


NUMBER = r"-?(?:\d[\d,]*\.?\d*|\.\d+)"
GRADE = r"(?:DIESEL\w*|ULP\d+|LRP\d*|UNLEADED\w*)"
MAX_REPORT_PAGES = 5
PERIOD_PATTERN = re.compile(
	r"From:\s*(\d{4}-\d\d-\d\d\s+\d\d:\d\d:\d\d)\s+"
	r"To:\s*(\d{4}-\d\d-\d\d\s+\d\d:\d\d:\d\d)"
)
NOZZLE_PATTERN = re.compile(
	rf"^[ \t]*(\d+)\s+({GRADE})\s+({NUMBER})\s+({NUMBER})\s+"
	rf"({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*$",
	re.MULTILINE | re.IGNORECASE,
)
FUEL_SALES_PATTERN = re.compile(
	rf"^[ \t]*({GRADE})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+(\d+(?:\.\d+)?)\s+({NUMBER})\s*$",
	re.MULTILINE | re.IGNORECASE,
)
TANK_READING_PATTERN = re.compile(
	rf"^[ \t]*({GRADE})\s+({NUMBER})\s*@\s*(\d{{4}}-\d\d-\d\d\s+\d\d:\d\d:\d\d)\s+"
	rf"({NUMBER})\s+({NUMBER})\s*@\s*(\d{{4}}-\d\d-\d\d\s+\d\d:\d\d:\d\d)\s*$",
	re.MULTILINE | re.IGNORECASE,
)
NOZZLE_ROW_CANDIDATE_PATTERN = re.compile(r"^[ \t]*\d+\s+\S+.*$", re.MULTILINE)
GRADE_ROW_CANDIDATE_PATTERN = re.compile(
	rf"^[ \t]*[A-Z][A-Z0-9_-]*\s+{NUMBER}\s+{NUMBER}\s+{NUMBER}\s+\d+(?:\.\d+)?\s+{NUMBER}\s*$",
	re.MULTILINE | re.IGNORECASE,
)
TANK_ROW_CANDIDATE_PATTERN = re.compile(
	rf"^[ \t]*[A-Z][A-Z0-9_-]*\s+{NUMBER}\s*@\s*\d{{4}}-\d\d-\d\d\s+\d\d:\d\d:\d\d\s+{NUMBER}\s+{NUMBER}\s*@",
	re.MULTILINE | re.IGNORECASE,
)
NOZZLE_TOTAL_PATTERN = re.compile(
	rf"^[ \t]*Totals:\s*({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*$",
	re.MULTILINE | re.IGNORECASE,
)
FUEL_SALES_TOTAL_PATTERN = re.compile(
	rf"^[ \t]*Totals:\s*({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})(?:\s+{NUMBER})?\s*$",
	re.MULTILINE | re.IGNORECASE,
)
POS_FUEL_ROW_PATTERN = re.compile(
	rf"^[ \t]*Fuel sales:\s*({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*$",
	re.MULTILINE | re.IGNORECASE,
)
POS_DRY_ROW_PATTERN = re.compile(
	rf"^[ \t]*Dry sales:\s*({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*$",
	re.MULTILINE | re.IGNORECASE,
)
TOTAL_CREDIT_CARD_PATTERN = re.compile(
	rf"Total\s+Credit\s+Card\s*:?\s*({NUMBER})",
	re.IGNORECASE,
)
TOTAL_FLEET_CARD_PATTERN = re.compile(
	rf"Total\s+Fleet\s+Card\s*:?\s*({NUMBER})",
	re.IGNORECASE,
)
TOTAL_DEBIT_CARD_PATTERN = re.compile(
	rf"Total\s+Debit\s+Card\s*:?\s*({NUMBER})",
	re.IGNORECASE,
)
POS_NOZZLE_SECTION_PATTERN = re.compile(
	r"Sales Report from Pumps \((?:Mechanical|Machinical) totalizer movement by nozzle\)",
	re.IGNORECASE,
)


class ReportParseError(ValueError):
	"""Raised when a PDF does not contain the expected report data."""


def _decimal(value):
	value = value.replace(",", "").strip()
	if value.startswith("."):
		value = "0" + value
	elif value.startswith("-."):
		value = "-0" + value[1:]
	try:
		return Decimal(value)
	except InvalidOperation as exc:
		raise ReportParseError(f"Invalid number in report: {value!r}") from exc


def extract_text(file_obj):
	try:
		import pdfplumber
	except ImportError as exc:
		raise ReportParseError("PDF support is not installed. Install the project requirements.") from exc
	try:
		with pdfplumber.open(file_obj) as pdf:
			if len(pdf.pages) > MAX_REPORT_PAGES:
				raise ReportParseError(f"The report must have no more than {MAX_REPORT_PAGES} pages.")
			return "\n".join(page.extract_text() or "" for page in pdf.pages)
	except ReportParseError:
		raise
	except Exception as exc:
		raise ReportParseError("The uploaded file could not be read as a PDF.") from exc


def parse_report(text):
	if "Business Analysis Report" not in text:
		raise ReportParseError("This is not a Business Analysis Report.")
	period_match = PERIOD_PATTERN.search(text)
	if not period_match:
		raise ReportParseError("Report period not found.")
	try:
		period_from = datetime.strptime(period_match.group(1), "%Y-%m-%d %H:%M:%S")
		period_to = datetime.strptime(period_match.group(2), "%Y-%m-%d %H:%M:%S")
	except ValueError as exc:
		raise ReportParseError("The report contains an invalid period.") from exc

	transactions_start = text.find("Transactions summary as per POS system")
	if transactions_start < 0:
		raise ReportParseError("Transactions summary section not found.")
	transactions_end = text.find("Fuel Sales Summary", transactions_start)
	if transactions_end < 0:
		raise ReportParseError("Fuel Sales Summary section not found.")
	pos_fuel_match = POS_FUEL_ROW_PATTERN.search(text[transactions_start:transactions_end])
	if not pos_fuel_match:
		raise ReportParseError("POS fuel sales value was not found.")
	pos_fuel_value_exvat = _decimal(pos_fuel_match.group(2))
	pos_dry_match = POS_DRY_ROW_PATTERN.search(text[transactions_start:transactions_end])
	pos_shop_value_incl_vat = _decimal(pos_dry_match.group(4)) if pos_dry_match else None

	section_start = text.find("Electronic totalizer movement")
	if section_start < 0:
		raise ReportParseError("Electronic totalizer movement section not found.")
	section_end_match = re.search(
		r"Sales Report from Pumps \((?:Mechanical|Machinical) totalizer movement by nozzle\)"
		r"|Sales Report: calculated",
		text[section_start:],
		re.IGNORECASE,
	)
	section = text[section_start:section_start + section_end_match.start()] if section_end_match else text[section_start:]
	nozzle_candidates = NOZZLE_ROW_CANDIDATE_PATTERN.findall(section)
	nozzles = [
		{
			"number": int(match.group(1)),
			"grade": match.group(2),
			"opening": _decimal(match.group(3)),
			"closing": _decimal(match.group(4)),
			"totalizer_litres": _decimal(match.group(5)),
			"sales_litres": _decimal(match.group(6)),
			"variance": _decimal(match.group(7)),
		}
		for match in NOZZLE_PATTERN.finditer(section)
	]
	if not nozzles:
		raise ReportParseError("No electronic nozzle rows were found in the report.")
	if len(nozzle_candidates) != len(nozzles):
		raise ReportParseError("Some numbered electronic nozzle rows could not be read. Check the report grade or layout.")
	if len({row["number"] for row in nozzles}) != len(nozzles):
		raise ReportParseError("The report contains duplicate nozzle numbers.")
	nozzle_totals_match = NOZZLE_TOTAL_PATTERN.search(section)
	if not nozzle_totals_match:
		raise ReportParseError("Electronic nozzle totals were not found.")
	reported_totalizer_litres = _decimal(nozzle_totals_match.group(1))
	reported_pump_sales_litres = _decimal(nozzle_totals_match.group(2))

	fuel_sales_start = text.find("Fuel Sales Summary")
	fuel_sales_end = text.find("Payment and Bankable", fuel_sales_start)
	if fuel_sales_start < 0 or fuel_sales_end < 0:
		raise ReportParseError("Fuel Sales Summary section not found.")
	fuel_sales_section = text[fuel_sales_start:fuel_sales_end]
	fuel_sales_candidates = GRADE_ROW_CANDIDATE_PATTERN.findall(fuel_sales_section)
	fuel_sales = [
		{
			"grade": match.group(1),
			"litres": _decimal(match.group(2)),
			"value_exvat": _decimal(match.group(3)),
			"tax_vat": _decimal(match.group(4)),
			"sales_count": _decimal(match.group(5)),
			"average": _decimal(match.group(6)),
		}
		for match in FUEL_SALES_PATTERN.finditer(text[fuel_sales_start:fuel_sales_end])
	]
	if not fuel_sales:
		raise ReportParseError("No fuel sales rows were found in the report.")
	if len(fuel_sales_candidates) != len(fuel_sales):
		raise ReportParseError("Some fuel sales grade rows could not be read. Check the report grade or layout.")
	fuel_sales_total_match = FUEL_SALES_TOTAL_PATTERN.search(fuel_sales_section)
	if not fuel_sales_total_match:
		raise ReportParseError("Fuel Sales Summary totals were not found.")
	reported_pos_sales_litres = _decimal(fuel_sales_total_match.group(1))

	payment_start = text.find("Payment and Bankable cash analysis summary")
	payment_end = text.find("Fuel Sales by Grade", payment_start)
	if payment_start < 0 or payment_end < 0:
		raise ReportParseError("Payment and Bankable cash analysis summary section not found.")
	payment_section = text[payment_start:payment_end]
	account_matches = re.findall(rf"Total Account:\s*({NUMBER})", payment_section, re.IGNORECASE)
	payout_matches = re.findall(rf"Total Payouts:\s*({NUMBER})", payment_section, re.IGNORECASE)
	money_drops_match = re.search(rf"Money Drops for Period:\s*({NUMBER})", payment_section, re.IGNORECASE)
	credit_card_match = TOTAL_CREDIT_CARD_PATTERN.search(payment_section)
	fleet_card_match = TOTAL_FLEET_CARD_PATTERN.search(payment_section)
	debit_card_match = TOTAL_DEBIT_CARD_PATTERN.search(payment_section)
	float_match = re.search(rf"Total Left in Drawer:\s*({NUMBER})", payment_section, re.IGNORECASE)
	cheque_match = re.search(rf"Total\s*Cheque:\s*({NUMBER})", payment_section, re.IGNORECASE)
	if not account_matches or not payout_matches or not money_drops_match:
		raise ReportParseError("Required cash-up totals were not found in the payment summary.")
	cash_up = {
		"forecourt_total": pos_fuel_value_exvat,
		"safedrops": _decimal(money_drops_match.group(1)),
		"account_1": _decimal(account_matches[0]),
		"payout_1": _decimal(payout_matches[0]),
		"shop_total": pos_shop_value_incl_vat,
		"cash_float": _decimal(float_match.group(1)) if float_match else None,
		"credit_card_total": _decimal(credit_card_match.group(1)) if credit_card_match else None,
		"fleet_card_total": _decimal(fleet_card_match.group(1)) if fleet_card_match else None,
		"debit_card_total": _decimal(debit_card_match.group(1)) if debit_card_match else None,
		"voucher_1": _decimal(cheque_match.group(1)) if cheque_match else None,
	}
	mechanical_nozzles = []
	mechanical_section_match = POS_NOZZLE_SECTION_PATTERN.search(text)
	if mechanical_section_match:
		mechanical_start = mechanical_section_match.end()
		mechanical_end_match = re.search(r"Sales Report(?::| from Pumps)", text[mechanical_start:], re.IGNORECASE)
		mechanical_section = text[
			mechanical_start:mechanical_start + mechanical_end_match.start()
		] if mechanical_end_match else text[mechanical_start:]
		mechanical_candidates = NOZZLE_ROW_CANDIDATE_PATTERN.findall(mechanical_section)
		mechanical_nozzles = [
			{
				"number": int(match.group(1)),
				"grade": match.group(2),
				"opening": _decimal(match.group(3)),
				"closing": _decimal(match.group(4)),
				"totalizer_litres": _decimal(match.group(5)),
				"sales_litres": _decimal(match.group(6)),
				"variance": _decimal(match.group(7)),
			}
			for match in NOZZLE_PATTERN.finditer(mechanical_section)
		]
		if len(mechanical_candidates) != len(mechanical_nozzles):
			raise ReportParseError("Some numbered mechanical nozzle rows could not be read.")
		if len({row["number"] for row in mechanical_nozzles}) != len(mechanical_nozzles):
			raise ReportParseError("The report contains duplicate mechanical nozzle numbers.")

	opening_start = text.find("Opening Values")
	closing_start = text.find("Closing Values", opening_start)
	if opening_start < 0 or closing_start < 0:
		raise ReportParseError("Opening and closing tank values were not found.")
	opening_rows = [
		match.groups()
		for match in TANK_READING_PATTERN.finditer(text[opening_start:closing_start])
	]
	opening_candidates = TANK_ROW_CANDIDATE_PATTERN.findall(text[opening_start:closing_start])
	closing_rows = [
		match.groups()
		for match in TANK_READING_PATTERN.finditer(text[closing_start:])
	]
	closing_candidates = TANK_ROW_CANDIDATE_PATTERN.findall(text[closing_start:])
	if not opening_rows or len(opening_rows) != len(closing_rows):
		raise ReportParseError("Opening and closing tank rows do not match.")
	if len(opening_candidates) != len(opening_rows) or len(closing_candidates) != len(closing_rows):
		raise ReportParseError("Some tank rows could not be read. Check the report grade or layout.")
	tank_readings = []
	grade_positions = {}
	for opening, closing in zip(opening_rows, closing_rows):
		opening_grade, opening_dip, opening_dip_date, opening_variance, opening_volume, opening_volume_date = opening
		closing_grade, closing_dip, closing_dip_date, closing_variance, closing_volume, closing_volume_date = closing
		if opening_grade.casefold() != closing_grade.casefold():
			raise ReportParseError("Opening and closing tank grades do not match.")
		grade = opening_grade.upper()
		grade_positions[grade] = grade_positions.get(grade, 0) + 1
		try:
			tank_readings.append({
				"row": len(tank_readings) + 1,
				"grade": grade,
				"grade_index": grade_positions[grade],
				"opening_dip_level": _decimal(opening_dip),
				"opening_dip_date": datetime.strptime(opening_dip_date, "%Y-%m-%d %H:%M:%S"),
				"opening_variance": _decimal(opening_variance),
				"opening": _decimal(opening_volume),
				"opening_date": datetime.strptime(opening_volume_date, "%Y-%m-%d %H:%M:%S"),
				"closing_dip_level": _decimal(closing_dip),
				"closing_dip_date": datetime.strptime(closing_dip_date, "%Y-%m-%d %H:%M:%S"),
				"closing_variance": _decimal(closing_variance),
				"closing": _decimal(closing_volume),
				"closing_date": datetime.strptime(closing_volume_date, "%Y-%m-%d %H:%M:%S"),
			})
		except ValueError as exc:
			raise ReportParseError("A tank row contains an invalid date or value.") from exc
	warnings = []
	for row in nozzles:
		if abs((row["closing"] - row["opening"]) - row["totalizer_litres"]) > Decimal("0.01"):
			warnings.append(f"Nozzle {row['number']}: opening/closing movement does not match the reported totalizer.")
	if abs(sum((row["totalizer_litres"] for row in nozzles), Decimal("0")) - reported_totalizer_litres) > Decimal("0.01"):
		raise ReportParseError("Numbered nozzle totalizers do not match the report total.")
	if abs(sum((row["sales_litres"] for row in nozzles), Decimal("0")) - reported_pump_sales_litres) > Decimal("0.01"):
		raise ReportParseError("Nozzle fuel sales do not match the pump report total.")
	if abs(sum((row["litres"] for row in fuel_sales), Decimal("0")) - reported_pos_sales_litres) > Decimal("0.01"):
		raise ReportParseError("Fuel sales grade rows do not match the POS report total.")
	if abs(reported_pump_sales_litres - reported_pos_sales_litres) > Decimal("0.01"):
		warnings.append("Pump fuel sales do not match the POS fuel sales total.")
	return {
		"period_from": period_from,
		"period_to": period_to,
		"business_date": period_from.date(),
		"nozzles": nozzles,
		"mechanical_nozzles": mechanical_nozzles,
		"fuel_sales": fuel_sales,
		"tank_readings": tank_readings,
		"cash_up": cash_up,
		"warnings": warnings,
	}


def parse_pdf(file_obj):
	return parse_report(extract_text(file_obj))