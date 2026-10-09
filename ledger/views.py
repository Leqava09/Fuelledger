import json
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.db import connection, transaction
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User

from .forms import (
	BookInFuelForm,
	CashUpEntryForm,
	FirstHQUserForm,
	FuelSalesForm,
	FuelTransferForm,
	ElectronicReadingForm,
	MechanicalReadingForm,
	SiteGroupForm,
	SiteUserEditForm,
	TankReadingForm,
	SiteUserCreateForm,
)
from .models import (
	CashUpAdditional,
	CashUpShiftRecord,
	DailyFuelReading,
	FuelBooking,
	FuelTransfer,
	NozzleFuelReading,
	ShiftEntry,
	Site,
	SiteAuditEvent,
	SiteGroup,
	TankDipReading,
	UserProfile,
)
from .models import SITE_FORM_PERMISSION_CHOICES


SITE_FORM_LABELS = {
	"book-in-fuel": "Book IN Fuel",
	"transfer-fuel": "Transfer Fuel",
	"daily-fuel-readings": "Fuel Readings",
	"cash-up": "Cash Up",
}
FUEL_READING_FORM_LABELS = {
	"fuel-sales": "Fuel Sales",
	"tank-reading": "Tank Reading",
	"mechanical-readings": "Mechanical Readings",
	"electronic-readings": "Electronic Readings",
}
CASH_UP_TAB_LABELS = (
	("fuel", "Total Sales"),
	("cash", "Cash"),
	("vouchers", "Vouchers"),
	("sales-account", "Account"),
	("payouts", "Payouts"),
	("eft-driveway", "EFT"),
	("manual-shop", "Manual"),
	("cash-up-additional", "Additional"),
	("airtime", "Airtime"),
	("summary", "Summary"),
)


def _first_hq_user_setup_available():
	return not User.objects.exists() and not UserProfile.objects.filter(role=UserProfile.Role.HQ).exists()


class FuelLedgerLoginView(LoginView):
	template_name = "registration/login.html"

	def get_success_url(self):
		profile = _profile(self.request.user)
		if self.request.user.is_superuser or (profile and profile.role == UserProfile.Role.HQ):
			return reverse("hq-dashboard")
		if profile and profile.site_id:
			return reverse("site-dashboard", args=[profile.site.slug])
		return reverse("home")

	def get_context_data(self, **kwargs):
		context = super().get_context_data(**kwargs)
		context["first_hq_user_setup_available"] = _first_hq_user_setup_available()
		return context


def first_hq_user_setup(request):
	if not _first_hq_user_setup_available():
		raise Http404
	form = FirstHQUserForm(request.POST or None)
	if request.method == "POST" and form.is_valid():
		with transaction.atomic():
			if connection.vendor == "postgresql":
				with connection.cursor() as cursor:
					cursor.execute("SELECT pg_advisory_xact_lock(%s)", [48273190522])
			if not _first_hq_user_setup_available():
				raise Http404
			user = form.save(commit=False)
			user.is_staff = True
			user.save()
			UserProfile.objects.create(user=user, role=UserProfile.Role.HQ)
		auth_login(request, user)
		return redirect("hq-dashboard")
	return render(request, "registration/first_hq_user.html", {"form": form})


def _selected_business_date(request):
	today = timezone.localdate()
	try:
		selected_date = date.fromisoformat(request.GET.get("date", ""))
	except (TypeError, ValueError):
		selected_date = today
	if selected_date > today:
		selected_date = today
	return selected_date, today


def _profile(user):
	if user.is_superuser:
		return None
	try:
		return user.fuel_profile
	except Exception:
		return None


def _can_manage_site_users(user, site):
	if user.is_superuser:
		return True
	profile = _profile(user)
	return bool(profile and (profile.role == "HQ" or (profile.site_id == site.id and profile.can_manage_users)))


def _site_form_permissions(user, site):
	all_permissions = {permission for permission, _ in SITE_FORM_PERMISSION_CHOICES}
	if user.is_superuser:
		return all_permissions
	profile = _profile(user)
	if not profile or profile.site_id != site.id:
		return set()
	if profile.role == UserProfile.Role.HQ:
		return all_permissions
	permissions = set(profile.form_permissions)
	for group_permissions in SiteGroup.objects.filter(site=site, users=user).values_list("form_permissions", flat=True):
		permissions.update(group_permissions)
	return permissions


def _can_access_site_form(user, site, form_key):
	if form_key == "transfer-fuel":
		form_key = "book-in-fuel"
	if form_key == "fuel-sales":
		form_key = "tank-reading"
	permissions = _site_form_permissions(user, site)
	if form_key == "daily-fuel-readings":
		return any(
			_site_uses_fuel_reading(site, key)
			and (key in permissions or (key == "fuel-sales" and "tank-reading" in permissions))
			for key in FUEL_READING_FORM_LABELS
		)
	return form_key in permissions


def _site_uses_fuel_reading(site, reading_type):
	return getattr(site, {
		"tank-reading": "use_tank_readings",
		"fuel-sales": "use_fuel_sales",
		"mechanical-readings": "use_mechanical_readings",
		"electronic-readings": "use_electronic_readings",
	}[reading_type])


def _site_fuel_reading_types(site):
	return [key for key in FUEL_READING_FORM_LABELS if _site_uses_fuel_reading(site, key)]


def _fuel_workflow_redirect(request, site, selected_date, shift_label, form_key, label, save_mode, next_form=None, stay_on_continue=False):
	messages.success(request, f"{label} saved.")
	date_query = f"date={selected_date.isoformat()}&shift={shift_label}"
	draft_saved_query = f"&workflow_saved={form_key}"
	if save_mode == "exit":
		return redirect(f"{reverse('site-dashboard', args=[site.slug])}?{date_query}{draft_saved_query}")
	if save_mode == "continue":
		if stay_on_continue:
			if form_key in FUEL_READING_FORM_LABELS:
				destination = reverse("fuel-reading-entry", args=[site.slug, form_key])
			else:
				destination = reverse("blank-site-form", args=[site.slug, form_key])
			return redirect(f"{destination}?{date_query}{draft_saved_query}")
		if next_form is None:
			return redirect(f"{reverse('site-dashboard', args=[site.slug])}?{date_query}{draft_saved_query}")
		if next_form in FUEL_READING_FORM_LABELS:
			destination = reverse("fuel-reading-entry", args=[site.slug, next_form])
		else:
			destination = reverse("blank-site-form", args=[site.slug, next_form])
		return redirect(f"{destination}?{date_query}{draft_saved_query}")
	if form_key in FUEL_READING_FORM_LABELS:
		destination = reverse("fuel-reading-entry", args=[site.slug, form_key])
	else:
		destination = reverse("blank-site-form", args=[site.slug, form_key])
	return redirect(f"{destination}?{date_query}{draft_saved_query}")


def _record_audit(site, actor, category, object_label, action, before=None, after=None):
	return SiteAuditEvent.objects.create(
		site=site,
		actor=actor,
		origin=SiteAuditEvent.Origin.SITE,
		category=category,
		object_label=object_label,
		action=action,
		before=before or {},
		after=after or {},
	)


@login_required
def home(request):
	profile = _profile(request.user)
	if request.user.is_superuser or (profile and profile.role == "HQ"):
		return redirect("hq-dashboard")
	if profile and profile.site_id:
		return redirect("site-dashboard", slug=profile.site.slug)
	return HttpResponseForbidden("Your account needs a role and site assignment.")


@login_required
def hq_dashboard(request):
	if not request.user.is_superuser and (not _profile(request.user) or _profile(request.user).role != "HQ"):
		return HttpResponseForbidden("HQ access is restricted to HQ users.")
	sites = Site.objects.filter(is_active=True).order_by("name")
	return render(request, "ledger/hq_dashboard.html", {"sites": sites})


@login_required
def site_dashboard(request, slug):
	site = request.current_site
	selected_date, today = _selected_business_date(request)
	selected_shift = request.GET.get("shift", "")
	if selected_shift not in site.get_shift_names():
		selected_shift = ""
	permissions = _site_form_permissions(request.user, site)
	return render(request, "ledger/site_dashboard.html", {
		"site": site,
		"tank_count": site.tanks.count(),
		"speed_point_count": site.speed_points.count(),
		"manual_shop_count": site.manual_shop_points.count(),
		"selected_date": selected_date,
		"today": today,
		"shift_names": site.get_shift_names(),
		"selected_shift": selected_shift,
		"shift_label": selected_shift,
		"site_forms": {
			key: label
			for key, label in SITE_FORM_LABELS.items()
			if key not in {"daily-fuel-readings", "cash-up"}
			and (key in permissions or (key == "transfer-fuel" and "book-in-fuel" in permissions))
		},
		"fuel_reading_forms": [
			{"key": key, "label": label}
			for key, label in FUEL_READING_FORM_LABELS.items()
			if _site_uses_fuel_reading(site, key)
			and (key in permissions or (key == "fuel-sales" and "tank-reading" in permissions))
		],
		"cash_up_tabs": [
			{"key": key, "label": tab_label}
			for key, tab_label in _cash_up_tab_labels(site)
		] if "cash-up" in permissions else [],
		"can_manage_users": _can_manage_site_users(request.user, site),
		"history_count": site.audit_events.count(),
	})


@login_required
def blank_site_form(request, slug, form_key):
	label = SITE_FORM_LABELS.get(form_key)
	if label is None:
		raise Http404
	selected_date, today = _selected_business_date(request)
	site = request.current_site
	if not _can_access_site_form(request.user, site, form_key):
		return HttpResponseForbidden("Your account cannot access this form at this site.")
	shift_label = request.POST.get("shift_label") or request.GET.get("shift", "")
	if shift_label not in site.get_shift_names():
		return redirect(f"{reverse('site-dashboard', args=[site.slug])}?date={selected_date.isoformat()}")
	if form_key == "book-in-fuel":
		return _book_in_fuel(request, label, selected_date, today, shift_label)
	if form_key == "transfer-fuel":
		return _transfer_fuel(request, selected_date, shift_label)
	if form_key == "daily-fuel-readings":
		return redirect(f"{reverse('site-dashboard', args=[site.slug])}?date={selected_date.isoformat()}&shift={shift_label}")
	if form_key == "cash-up":
		return _cash_up_entry(request, selected_date, shift_label, "cash")
	return render(request, "ledger/blank_site_form.html", {
		"site": site,
		"form_key": form_key,
		"form_label": label,
		"selected_date": selected_date,
		"shift_label": shift_label,
	})


def _transfer_fuel(request, selected_date, shift_label):
	site = request.current_site
	form = FuelTransferForm(
		site,
		data=request.POST if request.method == "POST" else None,
		initial={"business_date": selected_date, "shift_label": shift_label},
	)
	if request.method == "POST" and form.is_valid():
		save_mode = request.POST.get("save_mode", "tab")
		transfer = FuelTransfer.objects.create(
			site=site,
			from_tank=form.cleaned_data["from_tank"],
			to_tank=form.cleaned_data["to_tank"],
			business_date=form.cleaned_data["business_date"],
			shift_label=form.cleaned_data["shift_label"],
			amount_litres=form.cleaned_data["amount_litres"],
			created_by=request.user,
		)
		_record_audit(
			site,
			request.user,
			"Transfer Fuel",
			f"{selected_date.isoformat()} / {shift_label}",
			"created",
			after={
				"from_tank": str(transfer.from_tank),
				"to_tank": str(transfer.to_tank),
				"amount_litres": str(transfer.amount_litres),
			},
		)
		return _fuel_workflow_redirect(
			request,
			site,
			selected_date,
			shift_label,
			"transfer-fuel",
			"Fuel transfer",
			save_mode,
			stay_on_continue=True,
		)
	transfers = FuelTransfer.objects.filter(
		site=site,
		business_date=selected_date,
		shift_label=shift_label,
	).select_related("from_tank", "to_tank", "created_by")
	return render(request, "ledger/transfer_fuel.html", {
		"site": site,
		"form": form,
		"selected_date": selected_date,
		"shift_label": shift_label,
		"has_tanks": site.tanks.exists(),
		"transfers": transfers,
	})


def _book_in_fuel(request, label, selected_date, today, shift_label):
	site = request.current_site
	tanks = list(site.tanks.order_by("name"))
	bookings = list(
		FuelBooking.objects.filter(site=site, business_date=selected_date, shift_label=shift_label)
		.select_related("tank")
		.order_by("delivery_number", "tank__name")
	)
	initial_deliveries = {}
	for booking in bookings:
		delivery = initial_deliveries.setdefault(
			booking.delivery_number,
			{"truck_reference": booking.truck_reference, "quantities": {}},
		)
		delivery["quantities"][booking.tank_id] = booking.qty_litres
	initial_delivery_count = max(initial_deliveries, default=1)
	form = BookInFuelForm(
		tanks,
		site,
		data=request.POST if request.method == "POST" else None,
		initial={
			"business_date": selected_date,
			"shift_label": shift_label,
			"delivery_count": initial_delivery_count,
		},
		initial_deliveries=initial_deliveries,
	)
	try:
		visible_delivery_count = int(form["delivery_count"].value() or initial_delivery_count)
	except (TypeError, ValueError):
		visible_delivery_count = initial_delivery_count
	visible_delivery_count = min(max(visible_delivery_count, 1), FuelBooking.MAX_DELIVERIES_PER_SHIFT)
	if request.method == "POST" and form.is_valid():
		save_mode = request.POST.get("save_mode", "tab")
		before = [
			{
				"delivery": booking.delivery_number,
				"truck_reference": booking.truck_reference,
				"tank": booking.tank.name,
				"quantity": str(booking.qty_litres),
			}
			for booking in bookings
		]
		saved_deliveries = []
		for delivery_number, truck_field, tank_fields in form.delivery_fields[:form.cleaned_data["delivery_count"]]:
			truck_reference = form.cleaned_data[truck_field].strip()
			for tank, field_name in tank_fields:
				quantity = form.cleaned_data[field_name]
				if quantity is not None:
					saved_deliveries.append((delivery_number, truck_reference, tank, quantity))
		with transaction.atomic():
			FuelBooking.objects.filter(site=site, business_date=selected_date, shift_label=shift_label).delete()
			FuelBooking.objects.bulk_create([
				FuelBooking(
					site=site,
					tank=tank,
					business_date=form.cleaned_data["business_date"],
					shift_label=form.cleaned_data["shift_label"],
					delivery_number=delivery_number,
					truck_reference=truck_reference,
					qty_litres=quantity,
					created_by=request.user,
				)
				for delivery_number, truck_reference, tank, quantity in saved_deliveries
			])
		_record_audit(
			site,
			request.user,
			"Book IN Fuel",
			f"{selected_date.isoformat()} / {shift_label}",
			"saved",
			before={"deliveries": before},
			after={"deliveries": [
				{
					"delivery": delivery_number,
					"truck_reference": truck_reference,
					"tank": tank.name,
					"quantity": str(quantity),
				}
				for delivery_number, truck_reference, tank, quantity in saved_deliveries
			]},
		)
		return _fuel_workflow_redirect(
			request,
			site,
			form.cleaned_data["business_date"],
			shift_label,
			"book-in-fuel",
			"Fuel delivery quantities",
			save_mode,
			stay_on_continue=True,
		)
	delivery_rows = [
		(
			delivery_number,
			form[truck_field],
			[(tank, form[field_name]) for tank, field_name in tank_fields],
		)
		for delivery_number, truck_field, tank_fields in form.delivery_fields[:visible_delivery_count]
	]
	return render(request, "ledger/book_in_fuel.html", {
		"site": site,
		"form_label": label,
		"selected_date": selected_date,
		"shift_label": shift_label,
		"today": today,
		"form": form,
		"delivery_rows": delivery_rows,
		"visible_delivery_count": visible_delivery_count,
		"tanks_data": [{"id": tank.pk, "name": tank.name, "product": tank.product} for tank in tanks],
	})


@login_required
def cash_up_entry(request, slug, section):
	site = request.current_site
	tab_labels = _cash_up_tab_labels(site)
	section_label = dict(tab_labels).get(section)
	if section_label is None:
		raise Http404
	if not _can_access_site_form(request.user, site, "cash-up"):
		return HttpResponseForbidden("Your account cannot access this form at this site.")
	selected_date, _ = _selected_business_date(request)
	shift_label = request.POST.get("shift_label") or request.GET.get("shift", "")
	if shift_label not in site.get_shift_names():
		return redirect(f"{reverse('site-dashboard', args=[site.slug])}?date={selected_date.isoformat()}")
	return _cash_up_entry(request, selected_date, shift_label, section)


@login_required
def cash_up_reports(request, slug):
	site = request.current_site
	if not _can_access_site_form(request.user, site, "cash-up"):
		return HttpResponseForbidden("Your account cannot access cash-up reports at this site.")
	today = timezone.localdate()
	period, from_date, to_date, report_start, report_end = _report_date_range(request.GET, today)
	reference_date = from_date

	shift_names = site.get_shift_names()
	shift_filter = request.GET.get("shift", "all")
	if shift_filter not in shift_names:
		shift_filter = "all"
	selected_shifts = [shift_filter] if shift_filter != "all" else list(shift_names)
	additional_field_ids = list(CashUpAdditional.objects.filter(site=site).values_list("pk", flat=True))
	candidates = list(CashUpShiftRecord.objects.filter(
		site=site,
		business_date__range=(report_start, report_end),
		shift_label__in=selected_shifts,
	).only("business_date", "shift_label", "values"))
	shift_order = {name: index for index, name in enumerate(shift_names)}
	candidates.sort(key=lambda item: (item.business_date, shift_order.get(item.shift_label, -1)))

	base_summary = _cash_up_summary({}, additional_field_ids)
	aggregate_amounts = {row["key"]: Decimal("0") for row in base_summary["rows"]}
	aggregate_sales = Decimal("0")
	report_rows = []
	for record in candidates:
		summary = _cash_up_summary(record.values or {}, additional_field_ids)
		amounts = {row["key"]: row["amount"] for row in summary["rows"]}
		for key, amount in amounts.items():
			aggregate_amounts[key] += amount
		aggregate_sales += summary["total_sales"]
		report_rows.append({
			"business_date": record.business_date,
			"shift": record.shift_label,
			"amounts": amounts,
			"accounted_total": summary["accounted_total"],
			"total_sales": summary["total_sales"],
			"difference": summary["difference"],
		})
	aggregate_rows = [
		{**row, "amount": aggregate_amounts[row["key"]]}
		for row in base_summary["rows"]
	]
	aggregate_accounted = sum(aggregate_amounts.values(), Decimal("0"))
	period_labels = {"day": "Day", "week": "Week", "month": "Month", "year": "Year", "manual": "Manual date range"}
	return render(request, "ledger/cash_up_reports.html", {
		"site": site,
		"period": period,
		"period_label": period_labels[period],
		"reference_date": reference_date,
		"from_date": from_date,
		"to_date": to_date,
		"report_start": report_start,
		"report_end": report_end,
		"today": today,
		"shift_names": shift_names,
		"shift_filter": shift_filter,
		"additional_enabled": bool(additional_field_ids),
		"summary_rows": aggregate_rows,
		"summary_accounted_total": aggregate_accounted,
		"summary_total_sales": aggregate_sales,
		"summary_difference": aggregate_accounted - aggregate_sales,
		"report_rows": report_rows,
	})


@login_required
def fuel_reports(request, slug):
	site = request.current_site
	if not _can_access_site_form(request.user, site, "daily-fuel-readings"):
		return HttpResponseForbidden("Your account cannot access fuel reports at this site.")
	today = timezone.localdate()
	period, from_date, to_date, report_start, report_end = _report_date_range(request.GET, today)
	shift_names = site.get_shift_names()
	shift_filter = request.GET.get("shift", "all")
	if shift_filter not in shift_names:
		shift_filter = "all"
	use_tank_readings = site.use_tank_readings
	use_fuel_sales = site.use_fuel_sales
	use_mechanical_readings = site.use_mechanical_readings
	use_electronic_readings = site.use_electronic_readings
	selected_shifts = set(shift_names if shift_filter == "all" else [shift_filter])
	shift_order = {name: index for index, name in enumerate(shift_names)}
	readings = list(
		DailyFuelReading.objects.filter(
			site=site,
			business_date__gte=report_start - timedelta(days=365),
			business_date__lte=report_end,
		).prefetch_related("tank_readings__tank", "nozzle_readings__nozzle__tank")
	)
	readings.sort(key=lambda row: (row.business_date, shift_order.get(row.shift_label, -1)))
	booked_in_by_tank = {}
	transferred_in_by_tank = {}
	transferred_out_by_tank = {}
	transfer_count = 0
	transfer_total_litres = Decimal("0")
	if use_tank_readings:
		bookings = FuelBooking.objects.filter(
			site=site,
			business_date__gte=report_start - timedelta(days=365),
			business_date__lte=report_end,
		).select_related("tank")
		for booking in bookings:
			key = (booking.business_date, booking.shift_label, booking.tank_id)
			booked_in_by_tank[key] = booked_in_by_tank.get(key, Decimal("0")) + booking.qty_litres
		transfers = list(FuelTransfer.objects.filter(
			site=site,
			business_date__gte=report_start - timedelta(days=365),
			business_date__lte=report_end,
		).select_related("from_tank", "to_tank"))
		for transfer in transfers:
			key = (transfer.business_date, transfer.shift_label)
			out_key = (*key, transfer.from_tank_id)
			in_key = (*key, transfer.to_tank_id)
			transferred_out_by_tank[out_key] = transferred_out_by_tank.get(out_key, Decimal("0")) + transfer.amount_litres
			transferred_in_by_tank[in_key] = transferred_in_by_tank.get(in_key, Decimal("0")) + transfer.amount_litres
			if report_start <= transfer.business_date <= report_end and transfer.shift_label in selected_shifts:
				transfer_count += 1
				transfer_total_litres += transfer.amount_litres
	last_tank_dips = {}
	last_nozzle_readings = {}
	product_labels = {}
	period_products = {}
	detail_rows = []
	product_colors = {}
	product_color_names = ("tone-1", "tone-2", "tone-3", "tone-4", "tone-5", "tone-6")

	def product_key(name):
		return name.strip().casefold()

	def product_color(name):
		key = product_key(name)
		if key not in product_colors:
			product_colors[key] = product_color_names[len(product_colors) % len(product_color_names)]
		return product_colors[key]

	tank_balances = {}
	if use_tank_readings:
		tank_balances = {
			tank.pk: {
				"tank": tank,
				"product_color": product_color(tank.product),
				"balance": None,
				"business_date": None,
			}
			for tank in site.tanks.all()
		}
		latest_tank_readings = TankDipReading.objects.filter(
			tank__site=site,
			daily_reading__business_date__lte=report_end,
		).select_related("tank", "daily_reading").order_by(
			"tank_id",
			"-daily_reading__business_date",
			"-daily_reading_id",
		).distinct("tank_id")
		for tank_reading in latest_tank_readings:
			balance_row = tank_balances[tank_reading.tank_id]
			balance_row.update({
				"balance": tank_reading.dip_litres,
				"business_date": tank_reading.daily_reading.business_date,
			})

	def fresh_product(name):
		return {
			"product": name,
			"product_color": product_color(name),
			"tank_decrease": Decimal("0"),
			"booked_in": Decimal("0"),
			"fuel_sales": Decimal("0"),
			"mechanical": Decimal("0"),
			"electronic": Decimal("0"),
			"has_tank": False,
			"has_sales": False,
			"has_mechanical": False,
			"has_electronic": False,
		}

	for reading in readings:
		in_period = report_start <= reading.business_date <= report_end and reading.shift_label in selected_shifts
		current_products = {}

		def current_product(name):
			key = product_key(name)
			product_labels.setdefault(key, name.strip())
			return current_products.setdefault(key, fresh_product(product_labels[key]))

		if use_tank_readings:
			for tank_reading in reading.tank_readings.select_related("tank"):
				key = product_key(tank_reading.tank.product)
				previous = last_tank_dips.get(tank_reading.tank_id)
				if in_period:
					product = current_product(tank_reading.tank.product)
					movement_key = (reading.business_date, reading.shift_label, tank_reading.tank_id)
					booked_in = booked_in_by_tank.get(movement_key, Decimal("0"))
					transfer_in = transferred_in_by_tank.get(movement_key, Decimal("0"))
					transfer_out = transferred_out_by_tank.get(movement_key, Decimal("0"))
					product["booked_in"] += booked_in
					if previous is not None and previous[0] == key:
						product["tank_decrease"] += previous[1] - tank_reading.dip_litres + booked_in + transfer_in - transfer_out
						product["has_tank"] = True
				last_tank_dips[tank_reading.tank_id] = (key, tank_reading.dip_litres)

		if in_period and use_fuel_sales:
			for product_name, litres in (reading.fuel_sales or {}).items():
				product = current_product(product_name)
				product["fuel_sales"] += Decimal(str(litres or 0))
				product["has_sales"] = True

		if use_mechanical_readings or use_electronic_readings:
			selected_nozzle_sources = []
			if use_mechanical_readings:
				selected_nozzle_sources.append(("mechanical_reading", "mechanical", "has_mechanical"))
			if use_electronic_readings:
				selected_nozzle_sources.append(("electronic_reading", "electronic", "has_electronic"))
			for nozzle_reading in reading.nozzle_readings.select_related("nozzle__tank"):
				product_name = nozzle_reading.nozzle.tank.product
				key = product_key(product_name)
				previous = last_nozzle_readings.get(nozzle_reading.nozzle_id, {})
				if in_period:
					product = current_product(product_name)
					for reading_field, total_field, has_field in selected_nozzle_sources:
						current_value = getattr(nozzle_reading, reading_field)
						previous_value = previous.get(reading_field)
						if current_value is not None and previous_value is not None and previous.get("product") == key:
							product[total_field] += current_value - previous_value
							product[has_field] = True
				for reading_field, _, _ in selected_nozzle_sources:
					current_value = getattr(nozzle_reading, reading_field)
					if current_value is not None:
						previous[reading_field] = current_value
				previous["product"] = key
				last_nozzle_readings[nozzle_reading.nozzle_id] = previous

		if not in_period:
			continue
		for key, product in current_products.items():
			if key not in period_products:
				period_products[key] = fresh_product(product["product"])
			period_product = period_products[key]
			period_product["booked_in"] += product["booked_in"]
			for metric in ("tank_decrease", "fuel_sales", "mechanical", "electronic"):
				has_flag = {
					"tank_decrease": "has_tank",
					"fuel_sales": "has_sales",
					"mechanical": "has_mechanical",
					"electronic": "has_electronic",
				}[metric]
				has_metric = product[has_flag]
				if has_metric:
					period_product[metric] += product[metric]
					period_product[has_flag] = True
			row = _fuel_report_display_row(product)
			row.update({"business_date": reading.business_date, "shift": reading.shift_label})
			detail_rows.append(row)

	detail_rows.sort(
		key=lambda row: (row["business_date"], shift_order.get(row["shift"], -1)),
		reverse=True,
	)
	product_rows = []
	for product in sorted(period_products.values(), key=lambda row: row["product"].casefold()):
		product_rows.append(_fuel_report_display_row(product))
	period_labels = {"day": "Day", "week": "Week", "month": "Month", "year": "Year", "manual": "Manual date range"}
	return render(request, "ledger/fuel_reports.html", {
		"site": site,
		"period": period,
		"period_label": period_labels[period],
		"from_date": from_date,
		"to_date": to_date,
		"report_start": report_start,
		"report_end": report_end,
		"today": today,
		"shift_names": shift_names,
		"shift_filter": shift_filter,
		"use_tank_readings": use_tank_readings,
		"use_fuel_sales": use_fuel_sales,
		"use_mechanical_readings": use_mechanical_readings,
		"use_electronic_readings": use_electronic_readings,
		"transfer_count": transfer_count,
		"transfer_total_litres": transfer_total_litres,
		"tank_balances": list(tank_balances.values()),
		"product_rows": product_rows,
		"detail_rows": detail_rows,
	})


def _fuel_report_display_row(values):
	tank = values["tank_decrease"] if values["has_tank"] else None
	sales = values["fuel_sales"] if values["has_sales"] else None
	mechanical = values["mechanical"] if values["has_mechanical"] else None
	electronic = values["electronic"] if values["has_electronic"] else None
	display_values = {
		key: value
		for key, value in values.items()
		if key not in {"has_tank", "has_sales", "has_mechanical", "has_electronic"}
	}
	display_values.update({
		"tank_decrease": tank,
		"fuel_sales": sales,
		"mechanical": mechanical,
		"electronic": electronic,
		"tank_vs_sales": tank - sales if tank is not None and sales is not None else None,
		"mechanical_vs_sales": mechanical - sales if mechanical is not None and sales is not None else None,
		"electronic_vs_sales": electronic - sales if electronic is not None and sales is not None else None,
		"mechanical_vs_electronic": mechanical - electronic if mechanical is not None and electronic is not None else None,
		"tank_vs_mechanical": tank - mechanical if tank is not None and mechanical is not None else None,
		"tank_vs_electronic": tank - electronic if tank is not None and electronic is not None else None,
	})
	for comparison, positive_label, negative_label, positive_tone, negative_tone in (
		("tank_vs_sales", "Shortage", "Surplus", "shortage", "surplus"),
		("mechanical_vs_sales", "Above sales", "Below sales", "higher", "lower"),
		("electronic_vs_sales", "Above sales", "Below sales", "higher", "lower"),
	):
		variance = display_values[comparison]
		if variance is None:
			display_values[f"{comparison}_status"] = None
			display_values[f"{comparison}_tone"] = ""
		elif variance > 0:
			display_values[f"{comparison}_status"] = positive_label
			display_values[f"{comparison}_tone"] = positive_tone
		elif variance < 0:
			display_values[f"{comparison}_status"] = negative_label
			display_values[f"{comparison}_tone"] = negative_tone
		else:
			display_values[f"{comparison}_status"] = "Matched"
			display_values[f"{comparison}_tone"] = "matched"
	return display_values


def _report_date_range(query, today):
	period = query.get("period", "day")
	if period not in {"day", "week", "month", "year", "manual"}:
		period = "day"
	try:
		anchor_date = min(date.fromisoformat(query.get("from_date") or query.get("date", "")), today)
	except (TypeError, ValueError):
		try:
			anchor_date = min(date.fromisoformat(query.get("start_date", "")), today)
		except (TypeError, ValueError):
			anchor_date = today
	if period == "manual":
		from_date = anchor_date
		try:
			to_date = min(date.fromisoformat(query.get("to_date") or query.get("end_date", "")), today)
		except (TypeError, ValueError):
			to_date = from_date
		if to_date < from_date:
			from_date, to_date = to_date, from_date
	else:
		from_date = anchor_date
		if period == "week":
			to_date = anchor_date - timedelta(days=7)
		elif period == "month":
			previous_month = anchor_date.month - 1 or 12
			previous_year = anchor_date.year - (1 if anchor_date.month == 1 else 0)
			to_date = anchor_date.replace(
				year=previous_year,
				month=previous_month,
				day=min(anchor_date.day, calendar.monthrange(previous_year, previous_month)[1]),
			)
		elif period == "year":
			previous_year = anchor_date.year - 1
			to_date = anchor_date.replace(
				year=previous_year,
				day=min(anchor_date.day, calendar.monthrange(previous_year, anchor_date.month)[1]),
			)
		else:
			to_date = anchor_date
	return period, from_date, to_date, min(from_date, to_date), max(from_date, to_date)


def _cash_up_entry(request, selected_date, shift_label, section):
	site = request.current_site
	record = CashUpShiftRecord.objects.filter(site=site, business_date=selected_date, shift_label=shift_label).first()
	initial_values = record.values if record else {}
	previous_airtime_balance = _previous_airtime_balance(site, selected_date, shift_label) if section == "airtime" else None
	airtime_first_card_editable = section == "airtime" and _is_first_airtime_card(site, selected_date, shift_label)
	has_first_card_activity = any(
		initial_values.get(f"{field_name}_1", initial_values.get(field_name)) not in (None, "")
		for field_name in ("airtime_slip", "airtime_system", "airtime_loaded")
	)
	if section == "airtime" and previous_airtime_balance is not None and (not airtime_first_card_editable or not has_first_card_activity):
		first_slip = Decimal(str(initial_values.get("airtime_slip_1", initial_values.get("airtime_slip")) or 0))
		first_loaded = Decimal(str(initial_values.get("airtime_loaded_1", initial_values.get("airtime_loaded")) or 0))
		current_airtime_balance = previous_airtime_balance - first_slip + first_loaded
		initial_values = {
			**initial_values,
			"airtime_balance_1": str(current_airtime_balance),
			"airtime_balance": str(current_airtime_balance),
		}
	post_data = request.POST.copy() if request.method == "POST" else None
	form = CashUpEntryForm(
		site,
		section,
		data=post_data,
		initial={"business_date": selected_date, "shift_label": shift_label},
		initial_values=initial_values,
		airtime_first_card_editable=airtime_first_card_editable,
	)
	if request.method == "POST":
		save_mode = request.POST.get("save_mode", "tab")
		if save_mode == "exit":
			try:
				all_tab_values = json.loads(request.POST.get("all_tab_values") or "{}")
			except (TypeError, ValueError):
				all_tab_values = {}
			if not isinstance(all_tab_values, dict):
				all_tab_values = {}
			current_values = {
				key: value
				for key, value in request.POST.items()
				if key not in {"csrfmiddlewaretoken", "save_mode", "all_tab_values"}
			}
			all_tab_values[section] = {**all_tab_values.get(section, {}), **current_values}
			updated_values = dict(initial_values)
			validation_errors = []
			for tab_key, _ in _cash_up_tab_labels(site):
				tab_values = all_tab_values.get(tab_key)
				if not isinstance(tab_values, dict):
					continue
				data = {
					**tab_values,
					"business_date": selected_date.isoformat(),
					"shift_label": shift_label,
				}
				tab_form = CashUpEntryForm(
					site,
					tab_key,
					data=data,
					initial_values=updated_values,
					airtime_first_card_editable=tab_key == "airtime" and _is_first_airtime_card(site, selected_date, shift_label),
				)
				if not tab_form.is_valid():
					validation_errors.append(section_label_for(tab_key))
					continue
				previous_tab_airtime_balance = _previous_airtime_balance(site, selected_date, shift_label) if tab_key == "airtime" else None
				updated_values = _apply_cash_up_form_values(updated_values, tab_form, previous_airtime_balance=previous_tab_airtime_balance)
			if validation_errors:
				form.add_error(None, "Check the invalid entries in these sections before saving all: " + ", ".join(validation_errors))
			else:
				return _complete_cash_up_save(request, site, record, initial_values, updated_values, selected_date, shift_label, "exit", section)
		elif form.is_valid():
			updated_values = _apply_cash_up_form_values(initial_values, form, previous_airtime_balance=previous_airtime_balance)
			return _complete_cash_up_save(request, site, record, initial_values, updated_values, selected_date, shift_label, save_mode, section)
	else:
		save_mode = "tab"
	additional_fields = list(CashUpAdditional.objects.filter(site=site))
	summary = _cash_up_summary(initial_values, [field.pk for field in additional_fields])
	totals = summary["totals"]
	total_group_prefixes = {
		"eft-driveway": "eft_",
		"manual-shop": "manual_",
		"cash-up-additional": "additional_",
	}
	airtime_difference = Decimal(str(initial_values.get("airtime_slip") or 0)) - Decimal(str(initial_values.get("airtime_system") or 0))
	group_totals = {
		"cash": totals["cash"],
		"vouchers": totals["vouchers"],
		"sales_account": totals["sales_account"],
		"arrears_account": totals["arrears_account"],
		"fuel": totals["fuel"],
		"payouts": totals["payouts"],
		"eft-driveway": totals["eft_driveway"],
		"manual-shop": totals["manual_shop"],
		"cash-up-additional": summary["additional_total"],
	}
	repeat_groups = [
		{
			**group,
			"count_field": form[group["count_key"]],
			"total": group_totals[group["key"]],
		}
		for group in form.repeat_groups
	]
	airtime_rows = []
	if section == "airtime":
		if request.method == "POST":
			try:
				row_count = min(max(int(form.data.get("airtime_count", 1) or 1), 1), form.MAX_REPEAT_ROWS)
			except (TypeError, ValueError):
				row_count = 1
		else:
			row_count = form.fields["airtime_count"].initial
		for index in range(1, row_count + 1):
			row_fields = []
			for base, label in (("airtime_slip", "Airtime Slip"), ("airtime_system", "Airtime System"), ("airtime_loaded", "Airtime Loaded"), ("airtime_balance", "Airtime Balance")):
				field_name = f"{base}_{index}"
				if field_name in form.fields:
					row_fields.append((label, form[field_name]))
			if row_fields:
				try:
					difference = Decimal(str(form[f"airtime_slip_{index}"].value() or 0)) - Decimal(str(form[f"airtime_system_{index}"].value() or 0))
				except (ArithmeticError, TypeError, ValueError):
					difference = Decimal("0")
				shift_key = "airtime_shift_nr" if index == 1 else f"airtime_shift_nr_{index}"
				airtime_rows.append({"index": index, "fields": row_fields, "shift_field": form[shift_key], "difference": difference})
	return render(request, "ledger/cash_up.html", {
		"site": site,
		"form": form,
		"section": section,
		"section_label": section_label_for(section),
		"selected_date": selected_date,
		"shift_label": shift_label,
		"cash_up_tabs": [
			{"key": key, "label": label}
			for key, label in _cash_up_tab_labels(site)
		],
		"total_amount": group_totals.get(section, Decimal("0")),
		"group_totals": group_totals,
		"sales_combined_total": totals["sales_accounts_combined"],
		"summary_rows": summary["rows"],
		"summary_accounted_total": summary["accounted_total"],
		"summary_total_sales": summary["total_sales"],
		"summary_difference": summary["difference"],
		"repeat_groups": repeat_groups,
		"airtime_rows": airtime_rows,
		"airtime_difference": airtime_difference,
		"previous_airtime_balance": previous_airtime_balance,
		"show_total": section in {"cash", "vouchers", "sales-account", "fuel", "payouts", *total_group_prefixes},
		"total_group": section,
		"field_rows": [(label, form[key]) for key, label in form.entry_fields],
		"draft_key": f"{request.user.pk}:{site.pk}:{selected_date.isoformat()}:{shift_label}",
	})


def _apply_cash_up_form_values(values, form, previous_airtime_balance=None):
	updated_values = dict(values)
	if form.section == "fuel":
		for saved_key in list(updated_values):
			if saved_key.startswith("fuel_") or saved_key.startswith("fuel_product_"):
				updated_values.pop(saved_key, None)
	if form.section == "airtime":
		original_values = dict(updated_values)
		for saved_key in list(updated_values):
			if saved_key == "airtime_count" or (saved_key.startswith("airtime_shift_nr_") and saved_key[len("airtime_shift_nr_"):].isdigit()) or saved_key in {"airtime_slip", "airtime_system", "airtime_loaded", "airtime_balance"} or any(
				saved_key.startswith(f"{field_name}_") and saved_key[len(field_name) + 1:].isdigit()
				for field_name in ("airtime_slip", "airtime_system", "airtime_loaded", "airtime_balance")
			):
				updated_values.pop(saved_key, None)
		row_count = 1
		if form.is_bound:
			try:
				row_count = min(max(int(form.data.get("airtime_count", 1) or 1), 1), form.MAX_REPEAT_ROWS)
			except (TypeError, ValueError):
				row_count = 1
		else:
			row_count = max(1, max((int(key.split("_")[-1]) for key in updated_values if key.startswith("airtime_slip_") and key.split("_")[-1].isdigit()), default=1))
		total_slip = Decimal("0")
		total_system = Decimal("0")
		total_loaded = Decimal("0")
		if form.airtime_first_card_editable:
			first_balance = form.cleaned_data.get("airtime_balance_1")
			if first_balance in (None, ""):
				first_balance = form.cleaned_data.get("airtime_balance")
		else:
			first_balance = previous_airtime_balance
			if first_balance in (None, ""):
				first_balance = original_values.get("airtime_balance_1", original_values.get("airtime_balance"))
		running_balance = Decimal(str(first_balance or 0))
		for index in range(1, row_count + 1):
			values = {}
			shift_key = "airtime_shift_nr" if index == 1 else f"airtime_shift_nr_{index}"
			shift_number = form.cleaned_data.get(shift_key)
			updated_values[shift_key] = str(shift_number or "")
			for field_name in ("airtime_slip", "airtime_system", "airtime_loaded"):
				value = form.cleaned_data.get(f"{field_name}_{index}")
				if value in (None, "") and index == 1:
					value = form.cleaned_data.get(field_name)
				amount = Decimal(str(value or 0))
				values[field_name] = amount
				updated_values[f"{field_name}_{index}"] = str(value) if value not in (None, "") else ""
			total_slip += values["airtime_slip"]
			total_system += values["airtime_system"]
			total_loaded += values["airtime_loaded"]
			if index > 1 or not form.airtime_first_card_editable:
				running_balance = running_balance - values["airtime_slip"] + values["airtime_loaded"]
			updated_values[f"airtime_balance_{index}"] = str(running_balance)
		updated_values["airtime_slip"] = str(total_slip)
		updated_values["airtime_system"] = str(total_system)
		updated_values["airtime_loaded"] = str(total_loaded)
		updated_values["airtime_balance"] = str(running_balance)
		updated_values["airtime_count"] = row_count
	for key, _ in form.entry_fields:
		if form.section == "airtime":
			continue
		value = form.cleaned_data[key]
		if value in (None, ""):
			updated_values.pop(key, None)
		else:
			updated_values[key] = str(value)
	repeat_counts = dict(updated_values.get("repeat_counts", {}))
	for group in form.repeat_groups:
		repeat_counts[group["key"]] = form.cleaned_data[group["count_key"]]
		legacy_key = {
			"vouchers": "vouchers",
			"payouts": "payout",
			"sales_account": "sales_account",
			"arrears_account": "arrears_account",
		}.get(group["key"])
		if legacy_key:
			updated_values.pop(legacy_key, None)
	if repeat_counts:
		updated_values["repeat_counts"] = repeat_counts
	updated_values["totals"] = _cash_up_totals(updated_values)
	return updated_values


def _complete_cash_up_save(request, site, record, before, updated_values, selected_date, shift_label, save_mode, section):
	with transaction.atomic():
		if record is None:
			record = CashUpShiftRecord.objects.create(
				site=site,
				business_date=selected_date,
				shift_label=shift_label,
				created_by=request.user,
				values=updated_values,
			)
		else:
			record.values = updated_values
			record.save(update_fields=["values", "updated_at"])
	_record_audit(
		site,
		request.user,
		"Cash Up",
		f"{selected_date.isoformat()} / {shift_label}",
		"updated" if before else "created",
		before=before,
		after=updated_values,
	)
	if save_mode == "exit":
		messages.success(request, "All cash-up sections saved.")
		return redirect(
			f"{reverse('site-dashboard', args=[site.slug])}"
			f"?date={selected_date.isoformat()}&shift={shift_label}&cashup_saved=all&workflow_saved={section}"
		)
	messages.success(request, f"{section_label_for(section)} saved.")
	return redirect(
		f"{reverse('cash-up-entry', args=[site.slug, section])}"
		f"?date={selected_date.isoformat()}&shift={shift_label}&saved={section}&workflow_saved={section}"
	)


def _latest_airtime_balance(values):
	if not isinstance(values, dict):
		return None
	balance_values = []
	for key, value in values.items():
		if key.startswith("airtime_balance_") and key[len("airtime_balance_"):].isdigit():
			balance_values.append((int(key[len("airtime_balance_"):]), value))
		elif key == "airtime_balance" and value not in (None, ""):
			balance_values.append((0, value))
	if not balance_values:
		return None
	return Decimal(str(sorted(balance_values)[-1][1]))


def _is_first_airtime_card(site, selected_date, shift_label):
	shift_names = site.get_shift_names()
	if shift_label not in shift_names:
		return False
	airtime_record = Q(values__has_key="airtime_count")
	for key in ("airtime_shift_nr", "airtime_shift_nr_2", "airtime_slip", "airtime_slip_1", "airtime_balance", "airtime_balance_1"):
		airtime_record |= Q(**{f"values__has_key": key})
	candidates = CashUpShiftRecord.objects.filter(
		site=site,
		business_date__lte=selected_date,
		shift_label__in=shift_names,
	).filter(airtime_record).only("business_date", "shift_label")
	first_record = min(candidates, key=lambda item: (item.business_date, shift_names.index(item.shift_label)), default=None)
	return first_record is None or (first_record.business_date == selected_date and first_record.shift_label == shift_label)


def _previous_airtime_balance(site, selected_date, shift_label):
	shift_names = site.get_shift_names()
	if shift_label not in shift_names:
		return None
	shift_order = {name: index for index, name in enumerate(shift_names)}
	current_shift_order = shift_order[shift_label]
	candidates = CashUpShiftRecord.objects.filter(
		site=site,
		business_date__gte=selected_date - timedelta(days=365),
		business_date__lte=selected_date,
	).only("business_date", "shift_label", "values", "updated_at")
	for candidate in sorted(
		candidates,
		key=lambda item: (item.business_date, shift_order.get(item.shift_label, -1)),
		reverse=True,
	):
		if candidate.business_date == selected_date and shift_order.get(candidate.shift_label, -1) >= current_shift_order:
			continue
		value = _latest_airtime_balance(candidate.values)
		if value is not None:
			return value
	return None


def _cash_up_totals(values):
	def sum_group(prefix, legacy_key=None):
		group_values = [
			Decimal(str(value or 0))
			for key, value in values.items()
			if key.startswith(prefix) and key[len(prefix):].isdigit()
		]
		if not group_values and legacy_key:
			legacy_value = values.get(legacy_key)
			if legacy_value not in (None, ""):
				group_values.append(Decimal(str(legacy_value)))
		return sum(group_values, Decimal("0"))

	cash = Decimal(str(values.get("coins") or 0)) + Decimal(str(values.get("safedrops") or 0))
	sales_account = sum_group("sales_account_", "sales_account")
	arrears_account = sum_group("arrears_account_", "arrears_account")
	legacy_fuel_total = _fuel_total(values, sum_group)
	forecourt_total = Decimal(str(values.get("forecourt_total") or 0))
	shop_total = Decimal(str(values.get("shop_total") or 0))
	fuel_total = legacy_fuel_total
	if any(key in values for key in ("forecourt_total", "shop_total")):
		fuel_total = forecourt_total + shop_total
	return {
		"cash": str(cash),
		"vouchers": str(sum_group("voucher_", "vouchers")),
		"sales_account": str(sales_account),
		"arrears_account": str(arrears_account),
		"sales_accounts_combined": str(sales_account + arrears_account),
		"fuel": str(fuel_total),
		"payouts": str(sum_group("payout_", "payout")),
		"airtime_slip": str(sum_group("airtime_slip_", "airtime_slip")),
		"airtime_loaded": str(Decimal(str(values.get("airtime_loaded") or 0))),
		"eft_driveway": str(sum(Decimal(str(value or 0)) for key, value in values.items() if key.startswith("eft_"))),
		"manual_shop": str(sum(Decimal(str(value or 0)) for key, value in values.items() if key.startswith("manual_"))),
		"cash_up_additional": str(sum(Decimal(str(value or 0)) for key, value in values.items() if key.startswith("additional_"))),
	}


def _cash_up_summary(values, additional_field_ids=()):
	totals = _cash_up_totals(values)
	additional_total = sum(
		(Decimal(str(values.get(f"additional_{field_id}") or 0)) for field_id in additional_field_ids),
		Decimal("0"),
	)
	rows = [
		{"key": "cash", "label": "Total Cash", "amount": Decimal(totals["cash"])},
		{"key": "vouchers", "label": "Total Vouchers", "amount": Decimal(totals["vouchers"])},
		{"key": "accounts", "label": "Combined Sales and Arrears", "amount": Decimal(totals["sales_accounts_combined"])},
		{"key": "eft", "label": "Total EFT", "amount": Decimal(totals["eft_driveway"])},
		{"key": "manual", "label": "Total Manual", "amount": Decimal(totals["manual_shop"])},
	]
	if additional_field_ids:
		rows.append({"key": "additional", "label": "Total Additional", "amount": additional_total})
	rows.extend([
		{"key": "airtime_slip", "label": "Total Airtime Slip", "amount": Decimal(totals["airtime_slip"])},
		{"key": "payouts", "label": "Total Payouts", "amount": Decimal(totals["payouts"]), "class": "cash-up-summary-row--payouts"},
	])
	accounted_total = sum((row["amount"] for row in rows), Decimal("0"))
	total_sales = Decimal(totals["fuel"])
	return {
		"rows": rows,
		"accounted_total": accounted_total,
		"total_sales": total_sales,
		"difference": accounted_total - total_sales,
		"additional_total": additional_total,
		"totals": totals,
	}


def _fuel_total(values, legacy_sum_group):
	product_values = [
		Decimal(str(value or 0))
		for key, value in values.items()
		if key.startswith("fuel_product_")
	]
	if product_values:
		return sum(product_values, Decimal("0"))
	if any(key in values for key in ("forecourt_total", "shop_total")):
		return Decimal(str(values.get("forecourt_total") or 0)) + Decimal(str(values.get("shop_total") or 0))
	return legacy_sum_group("fuel_")


def section_label_for(section):
	return dict(CASH_UP_TAB_LABELS)[section]


def _cash_up_tab_labels(site):
	has_additional_fields = CashUpAdditional.objects.filter(site=site).exists()
	return tuple(
		(key, label)
		for key, label in CASH_UP_TAB_LABELS
		if key != "cash-up-additional" or has_additional_fields
	)


@login_required
def daily_fuel_reading_entry(request, slug):
	selected_date, _ = _selected_business_date(request)
	shift_label = request.POST.get("shift_label") or request.GET.get("shift", "")
	return redirect(
		f"{reverse('blank-site-form', args=[request.current_site.slug, 'daily-fuel-readings'])}"
		f"?date={selected_date.isoformat()}&shift={shift_label}"
	)


@login_required
def fuel_reading_entry(request, slug, reading_type):
	form_label = FUEL_READING_FORM_LABELS.get(reading_type)
	if form_label is None:
		raise Http404
	site = request.current_site
	if not _site_uses_fuel_reading(site, reading_type):
		raise Http404
	if not _can_access_site_form(request.user, site, reading_type):
		return HttpResponseForbidden("Your account cannot access this form at this site.")
	selected_date, _ = _selected_business_date(request)
	shift_label = request.POST.get("shift_label") or request.GET.get("shift", "")
	if shift_label not in site.get_shift_names():
		return redirect(f"{reverse('site-dashboard', args=[site.slug])}?date={selected_date.isoformat()}")
	tanks = list(site.tanks.prefetch_related("nozzles").order_by("name"))
	nozzles = [(tank, nozzle) for tank in tanks for nozzle in tank.nozzles.all()]
	reading = DailyFuelReading.objects.filter(
		site=site,
		business_date=selected_date,
		shift_label=shift_label,
	).first()
	previous_reading = None
	if reading_type in {"tank-reading", "mechanical-readings", "electronic-readings"}:
		previous_reading = _previous_daily_reading(site, selected_date, shift_label, reading_type)
	previous_tank_readings = {}
	previous_nozzle_readings = {}
	if previous_reading is not None:
		previous_tank_readings = {
			item.tank_id: (item.tank.product, item.dip_litres)
			for item in previous_reading.tank_readings.select_related("tank")
		}
		previous_field = {
			"mechanical-readings": "mechanical_reading",
			"electronic-readings": "electronic_reading",
		}.get(reading_type)
		if previous_field:
			previous_nozzle_readings = {
				item.nozzle_id: getattr(item, previous_field)
				for item in previous_reading.nozzle_readings.all()
				if getattr(item, previous_field) is not None
			}
	initial_tank_readings = {}
	initial_nozzle_readings = {}
	existing_nozzle_readings = {}
	if reading:
		initial_tank_readings = {
			item.tank_id: {"dip_litres": item.dip_litres, "depth_cm": item.depth_cm}
			for item in reading.tank_readings.all()
		}
		existing_nozzle_readings = {
			item.nozzle_id: item
			for item in reading.nozzle_readings.all()
		}
		if reading_type in {"mechanical-readings", "electronic-readings"}:
			reading_type_field = "mechanical_reading" if reading_type == "mechanical-readings" else "electronic_reading"
			initial_nozzle_readings = {
				nozzle_id: getattr(item, reading_type_field)
				for nozzle_id, item in existing_nozzle_readings.items()
				if getattr(item, reading_type_field) is not None
			}
	initial = {"business_date": selected_date, "shift_label": shift_label}
	form_class = {
		"tank-reading": TankReadingForm,
		"fuel-sales": FuelSalesForm,
		"mechanical-readings": MechanicalReadingForm,
		"electronic-readings": ElectronicReadingForm,
	}[reading_type]
	if reading_type == "fuel-sales":
		products = _site_fuel_products(tanks)
		form = form_class(
			site,
			products,
			data=request.POST if request.method == "POST" else None,
			initial=initial,
			initial_readings=reading.fuel_sales if reading else {},
		)
	else:
		form = form_class(
			site,
			data=request.POST if request.method == "POST" else None,
			initial=initial,
			initial_readings=initial_tank_readings if reading_type == "tank-reading" else initial_nozzle_readings,
			**({"previous_readings": previous_nozzle_readings} if reading_type in {"mechanical-readings", "electronic-readings"} else {}),
		)
	if request.method == "POST" and form.is_valid():
		fields = form.tank_fields if reading_type == "tank-reading" else form.product_fields if reading_type == "fuel-sales" else form.nozzle_fields
		if not fields:
			message = "Configure tanks with products for this site before entering fuel sales." if reading_type == "fuel-sales" else "Configure the relevant tanks or nozzles for this site before entering readings."
			form.add_error(None, message)
		else:
			if reading_type == "tank-reading":
				before = {
					str(item.tank_id): {
						"dip_litres": str(item.dip_litres),
						"depth_cm": str(item.depth_cm) if item.depth_cm is not None else None,
					}
					for item in reading.tank_readings.all()
				} if reading else {}
			elif reading_type == "fuel-sales":
				before = dict(reading.fuel_sales) if reading else {}
			else:
				field_name = "mechanical_reading" if reading_type == "mechanical-readings" else "electronic_reading"
				before = {
					str(nozzle_id): str(getattr(item, field_name))
					for nozzle_id, item in existing_nozzle_readings.items()
					if getattr(item, field_name) is not None
				}
			with transaction.atomic():
				if reading is None:
					reading = DailyFuelReading.objects.create(
						site=site,
						business_date=form.cleaned_data["business_date"],
						shift_label=shift_label,
						created_by=request.user,
						updated_by=request.user,
					)
				else:
					reading.updated_by = request.user
					reading.save(update_fields=["updated_by", "updated_at"])
				if reading_type == "tank-reading":
					for tank, dip_field_name, depth_field_name, capacity_field_name in form.tank_fields:
						TankDipReading.objects.update_or_create(
							daily_reading=reading,
							tank=tank,
							defaults={
								"dip_litres": form.cleaned_data[dip_field_name],
								"depth_cm": form.cleaned_data[depth_field_name],
							},
						)
				elif reading_type == "fuel-sales":
					reading.fuel_sales = {
						product: str(form.cleaned_data[field_name] or 0)
						for product, field_name in form.product_fields
					}
					reading.save(update_fields=["fuel_sales", "updated_by", "updated_at"])
				else:
					field_name = "mechanical_reading" if reading_type == "mechanical-readings" else "electronic_reading"
					other_field = "electronic_reading" if field_name == "mechanical_reading" else "mechanical_reading"
					for tank, nozzle, form_field in form.nozzle_fields:
						previous = existing_nozzle_readings.get(nozzle.pk)
						defaults = {
							field_name: form.cleaned_data[form_field],
							other_field: getattr(previous, other_field) if previous else None,
						}
						NozzleFuelReading.objects.update_or_create(
							daily_reading=reading,
							nozzle=nozzle,
							defaults=defaults,
						)
			if reading_type == "tank-reading":
				after = {
					str(item.tank_id): {
						"dip_litres": str(item.dip_litres),
						"depth_cm": str(item.depth_cm) if item.depth_cm is not None else None,
					}
					for item in reading.tank_readings.all()
				}
			elif reading_type == "fuel-sales":
				after = dict(reading.fuel_sales)
			else:
				after = {
					str(item.nozzle_id): str(getattr(item, field_name))
					for item in reading.nozzle_readings.all()
					if getattr(item, field_name) is not None
				}
			_record_audit(
				site,
				request.user,
				form_label,
				f"{selected_date.isoformat()} / {shift_label}",
				"updated" if before else "created",
				before=before,
				after=after,
			)
			available_reading_types = _site_fuel_reading_types(site)
			current_position = available_reading_types.index(reading_type)
			next_reading_type = (
				available_reading_types[current_position + 1]
				if current_position + 1 < len(available_reading_types)
				else None
			)
			return _fuel_workflow_redirect(
				request,
				site,
				form.cleaned_data["business_date"],
				form.cleaned_data["shift_label"],
				reading_type,
				form_label,
				request.POST.get("save_mode", "tab"),
				next_reading_type,
				stay_on_continue=True,
			)
	if reading_type == "tank-reading":
		field_rows = [
			{
				"tank": tank,
				"dip": form[dip_field_name],
				"depth": form[depth_field_name],
				"capacity": form[capacity_field_name],
				"previous": previous_tank_readings.get(tank.pk, (None, None))[1]
				if previous_tank_readings.get(tank.pk, (None, None))[0] is not None
				and previous_tank_readings[tank.pk][0].strip().casefold() == tank.product.strip().casefold()
				else None,
				"difference": _tank_reading_difference(
					previous_tank_readings.get(tank.pk, (None, None))[1]
					if previous_tank_readings.get(tank.pk, (None, None))[0] is not None
					and previous_tank_readings[tank.pk][0].strip().casefold() == tank.product.strip().casefold()
					else None,
					form[dip_field_name].value(),
				),
			}
			for tank, dip_field_name, depth_field_name, capacity_field_name in form.tank_fields
		]
	elif reading_type == "fuel-sales":
		field_rows = [(product, form[field_name]) for product, field_name in form.product_fields]
	else:
		field_rows = [
			{
				"tank": tank,
				"nozzle": nozzle,
				"reading": form[field_name],
				"previous": previous_nozzle_readings.get(nozzle.pk),
				"difference": _fuel_reading_difference(previous_nozzle_readings.get(nozzle.pk), form[field_name].value()),
			}
			for tank, nozzle, field_name in form.nozzle_fields
		]
	return render(request, "ledger/fuel_reading_form.html", {
		"site": site,
		"selected_date": selected_date,
		"shift_label": shift_label,
		"reading_type": reading_type,
		"form_label": form_label,
		"form": form,
		"field_rows": field_rows,
		"is_tank_reading": reading_type == "tank-reading",
		"is_fuel_sales": reading_type == "fuel-sales",
		"previous_reading": previous_reading,
		"history_key": reading_type,
		"fuel_reading_forms": [
			{"key": key, "label": label}
			for key, label in FUEL_READING_FORM_LABELS.items()
			if _site_uses_fuel_reading(site, key)
		],
		"active_reading_type": reading_type,
	})


def _site_fuel_products(tanks):
	products = {}
	for tank in tanks:
		product = tank.product.strip()
		if product:
			products.setdefault(product.casefold(), product)
	return list(products.values())


def _previous_daily_reading(site, selected_date, shift_label, reading_type):
	shift_names = site.get_shift_names()
	if shift_label not in shift_names:
		return None
	query = DailyFuelReading.objects.filter(
		site=site,
		business_date__gte=selected_date - timedelta(days=365),
		business_date__lte=selected_date,
	)
	if reading_type == "tank-reading":
		query = query.filter(tank_readings__isnull=False)
	elif reading_type == "mechanical-readings":
		query = query.filter(nozzle_readings__mechanical_reading__isnull=False)
	else:
		query = query.filter(nozzle_readings__electronic_reading__isnull=False)
	shift_order = {name: index for index, name in enumerate(shift_names)}
	current_order = shift_order[shift_label]
	candidates = query.distinct().prefetch_related("tank_readings__tank", "nozzle_readings")
	for candidate in sorted(
		candidates,
		key=lambda item: (item.business_date, shift_order.get(item.shift_label, -1)),
		reverse=True,
	):
		if candidate.business_date == selected_date and shift_order.get(candidate.shift_label, -1) >= current_order:
			continue
		return candidate
	return None


def _fuel_reading_difference(previous, current):
	if previous in (None, "") or current in (None, ""):
		return None
	try:
		return Decimal(str(current)) - Decimal(str(previous))
	except (ArithmeticError, TypeError, ValueError):
		return None


def _tank_reading_difference(previous, current):
	return _fuel_reading_difference(current, previous)


def _daily_reading_snapshot(reading):
	return {
		"fuel_sales": dict(reading.fuel_sales),
		"tanks": {
			str(item.tank_id): {"name": item.tank.name, "dip_litres": str(item.dip_litres)}
			for item in reading.tank_readings.select_related("tank")
		},
		"nozzles": {
			str(item.nozzle_id): {
				"name": item.nozzle.name,
				"mechanical": str(item.mechanical_reading),
				"electronic": str(item.electronic_reading),
			}
			for item in reading.nozzle_readings.select_related("nozzle")
		},
	}


def _require_site_user_manager(request):
	if not _can_manage_site_users(request.user, request.current_site):
		return HttpResponseForbidden("Your account cannot manage users or groups for this site.")
	return None


def _sync_site_group_members(group):
	if group.django_group_id:
		group.django_group.user_set.set(group.users.all())


@login_required
def site_admin_users(request, slug, user_id=None):
	denied = _require_site_user_manager(request)
	if denied:
		return denied
	site = request.current_site
	request.session["site_admin_site_id"] = site.pk
	if user_id is None:
		admin_url = reverse("site_admin:auth_user_changelist")
	else:
		get_object_or_404(User, pk=user_id, fuel_profile__site=site)
		admin_url = reverse("site_admin:auth_user_change", args=[user_id])
	return redirect(admin_url)


@login_required
def site_admin_user_add(request, slug):
	denied = _require_site_user_manager(request)
	if denied:
		return denied
	request.session["site_admin_site_id"] = request.current_site.pk
	return redirect(reverse("site_admin:auth_user_add"))


@login_required
def site_admin_groups(request, slug, group_id=None):
	denied = _require_site_user_manager(request)
	if denied:
		return denied
	site = request.current_site
	request.session["site_admin_site_id"] = site.pk
	if group_id is None:
		admin_url = reverse("site_admin:auth_group_changelist")
	else:
		get_object_or_404(SiteGroup, pk=group_id, site=site)
		admin_url = reverse("site_admin:auth_group_change", args=[group_id])
	return redirect(admin_url)


def _site_user_snapshot(user):
	profile = user.fuel_profile
	return {
		"username": user.username,
		"name": user.get_full_name(),
		"email": user.email,
		"is_active": user.is_active,
		"groups": list(user.fuel_site_groups.order_by("name").values_list("name", flat=True)),
		"phone_number": profile.phone_number,
		"form_permissions": list(profile.form_permissions),
		"can_configure": profile.can_configure,
		"can_manage_users": profile.can_manage_users,
	}


@login_required
def site_users(request, slug):
	denied = _require_site_user_manager(request)
	if denied:
		return denied
	site = request.current_site
	users = User.objects.filter(fuel_profile__site=site).prefetch_related("fuel_site_groups").order_by("username")
	groups = SiteGroup.objects.filter(site=site).prefetch_related("users").order_by("name")
	return render(request, "ledger/site_users.html", {"site": site, "users": users, "groups": groups})


@login_required
def site_user_create(request, slug):
	denied = _require_site_user_manager(request)
	if denied:
		return denied
	request.session["site_admin_site_id"] = request.current_site.pk
	return redirect(reverse("site_admin:auth_user_add"))


@login_required
def site_user_edit(request, slug, user_id):
	denied = _require_site_user_manager(request)
	if denied:
		return denied
	get_object_or_404(User, pk=user_id, fuel_profile__site=request.current_site)
	request.session["site_admin_site_id"] = request.current_site.pk
	return redirect(reverse("site_admin:auth_user_change", args=[user_id]))


@login_required
def site_groups(request, slug):
	denied = _require_site_user_manager(request)
	if denied:
		return denied
	site = request.current_site
	if request.method == "POST":
		form = SiteGroupForm(site, request.POST)
		if form.is_valid():
			group = form.save(commit=False)
			group.site = site
			group.form_permissions = form.cleaned_data["form_permissions"]
			group.save()
			form.save_m2m()
			_sync_site_group_members(group)
			_record_audit(site, request.user, "Site group", group.name, "created", after={
				"users": list(group.users.values_list("username", flat=True)),
				"form_permissions": group.form_permissions,
			})
			messages.success(request, f"Group {group.name} saved.")
			return redirect("site-groups", slug=site.slug)
	else:
		form = SiteGroupForm(site)
	return render(request, "ledger/site_groups.html", {
		"site": site,
		"groups": SiteGroup.objects.filter(site=site).prefetch_related("users"),
		"form": form,
	})


@login_required
def site_group_edit(request, slug, group_id):
	denied = _require_site_user_manager(request)
	if denied:
		return denied
	site = request.current_site
	group = get_object_or_404(SiteGroup, pk=group_id, site=site)
	form = SiteGroupForm(site, request.POST or None, instance=group)
	if request.method == "POST" and form.is_valid():
		before = {
			"name": group.name,
			"users": list(group.users.values_list("username", flat=True)),
			"form_permissions": list(group.form_permissions),
		}
		updated_group = form.save(commit=False)
		updated_group.form_permissions = form.cleaned_data["form_permissions"]
		updated_group.save()
		form.save_m2m()
		_sync_site_group_members(updated_group)
		_record_audit(site, request.user, "Site group", updated_group.name, "changed", before=before, after={
			"users": list(updated_group.users.values_list("username", flat=True)),
			"form_permissions": updated_group.form_permissions,
		})
		messages.success(request, f"Group {updated_group.name} updated.")
		return redirect("site-groups", slug=site.slug)
	return render(request, "ledger/site_group_form.html", {"site": site, "form": form, "group": group})


@login_required
def site_history(request, slug):
	site = request.current_site
	events = site.audit_events.filter(origin=SiteAuditEvent.Origin.SITE).select_related("actor")
	return render(request, "ledger/site_history.html", {
		"site": site,
		"events": events,
		"history_rows": _history_rows(events),
	})


@login_required
def site_form_history(request, slug, form_key):
	site = request.current_site
	form_label = FUEL_READING_FORM_LABELS.get(form_key) or SITE_FORM_LABELS.get(form_key)
	if form_label is None:
		raise Http404
	permission_key = form_key
	if form_key in FUEL_READING_FORM_LABELS:
		permission_key = form_key
	if not _can_access_site_form(request.user, site, permission_key):
		return HttpResponseForbidden("Your account cannot access this form history at this site.")
	selected_date, _ = _selected_business_date(request)
	shift_label = request.GET.get("shift", "")
	if shift_label not in site.get_shift_names():
		raise Http404
	object_label = f"{selected_date.isoformat()} / {shift_label}"
	category_filter = [form_label, "Daily Fuel Readings"] if form_key in FUEL_READING_FORM_LABELS else [form_label]
	events = site.audit_events.filter(
		origin=SiteAuditEvent.Origin.SITE,
		category__in=category_filter,
		object_label=object_label,
	).select_related("actor")
	if form_key in FUEL_READING_FORM_LABELS:
		form_url = reverse("fuel-reading-entry", args=[site.slug, form_key])
	else:
		form_url = reverse("blank-site-form", args=[site.slug, form_key])
	form_url = f"{form_url}?date={selected_date.isoformat()}&shift={shift_label}"
	return render(request, "ledger/site_history.html", {
		"site": site,
		"events": events,
		"history_rows": _history_rows(events),
		"form_label": form_label,
		"selected_date": selected_date,
		"shift_label": shift_label,
		"form_url": form_url,
	})


def _history_rows(events):
	rows = []
	for event in events:
		before = event.before if isinstance(event.before, dict) else {}
		after = event.after if isinstance(event.after, dict) else {}
		changed = []
		for key in dict.fromkeys([*before.keys(), *after.keys()]):
			if key in {"totals", "repeat_counts"} or before.get(key) == after.get(key):
				continue
			label = key.replace("_", " ").strip().capitalize()
			if key not in before:
				change = f"{label}: {_history_value(after[key])}"
			elif key not in after:
				change = f"{label} removed"
			else:
				change = f"{label}: {_history_value(before[key])} -> {_history_value(after[key])}"
			changed.append(change)
		description = "; ".join(changed) or event.action.replace("_", " ").capitalize()
		rows.append({"event": event, "description": description})
	return rows


def _history_value(value):
	if isinstance(value, (dict, list)):
		if isinstance(value, dict):
			value = ", ".join(f"{key}: {_history_value(item)}" for key, item in value.items())
		else:
			value = "; ".join(_history_value(item) for item in value)
	return str(value)


@login_required
def entry_list(request, slug):
	site = request.current_site
	entries = ShiftEntry.objects.filter(site=site).select_related("created_by")
	return render(request, "ledger/entry_list.html", {"site": site, "entries": entries})


@login_required
def entry_detail(request, slug, entry_id):
	entry = get_object_or_404(
		ShiftEntry.objects.select_related("created_by"),
		pk=entry_id,
		site=request.current_site,
	)
	sections = {}
	for item in entry.schema_snapshot:
		item["value"] = entry.values.get(item["key"], "")
		sections.setdefault(item["section"], []).append(item)
	return render(request, "ledger/entry_detail.html", {"site": request.current_site, "entry": entry, "sections": sections})
