"""Parser for fuel readings in POS Business Analysis Reports."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


NUMBER = r"-?(?:\d[\d,]*\.?\d*|\.\d+)"
GRADE = r"(?:DIESEL\w*|ULP\d+|LRP\d*|UNLEADED\w*)"
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
			return "\n".join(page.extract_text() or "" for page in pdf.pages)
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

	section_start = text.find("Electronic totalizer movement")
	if section_start < 0:
		raise ReportParseError("Electronic totalizer movement section not found.")
	section_end = text.find("Sales Report: calculated", section_start)
	section = text[section_start:] if section_end < 0 else text[section_start:section_end]
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
	if len({row["number"] for row in nozzles}) != len(nozzles):
		raise ReportParseError("The report contains duplicate nozzle numbers.")

	fuel_sales_start = text.find("Fuel Sales Summary")
	fuel_sales_end = text.find("Payment and Bankable", fuel_sales_start)
	if fuel_sales_start < 0 or fuel_sales_end < 0:
		raise ReportParseError("Fuel Sales Summary section not found.")
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

	opening_start = text.find("Opening Values")
	closing_start = text.find("Closing Values", opening_start)
	if opening_start < 0 or closing_start < 0:
		raise ReportParseError("Opening and closing tank values were not found.")
	opening_rows = [
		match.groups()
		for match in TANK_READING_PATTERN.finditer(text[opening_start:closing_start])
	]
	closing_rows = [
		match.groups()
		for match in TANK_READING_PATTERN.finditer(text[closing_start:])
	]
	if not opening_rows or len(opening_rows) != len(closing_rows):
		raise ReportParseError("Opening and closing tank rows do not match.")
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
	return {
		"period_from": period_from,
		"period_to": period_to,
		"business_date": period_from.date(),
		"nozzles": nozzles,
		"fuel_sales": fuel_sales,
		"tank_readings": tank_readings,
	}


def parse_pdf(file_obj):
	return parse_report(extract_text(file_obj))