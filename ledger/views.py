from datetime import date

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User

from .forms import BookInFuelForm, DailyFuelReadingForm, ShiftSelectionForm, SiteGroupForm, SiteUserCreateForm
from .models import (
	DailyFuelReading,
	FuelBooking,
	NozzleFuelReading,
	ShiftEntry,
	Site,
	SiteAuditEvent,
	SiteGroup,
	TankDipReading,
	UserProfile,
)


SITE_FORM_LABELS = {
	"book-in-fuel": "Book IN Fuel",
	"daily-fuel-readings": "Daily Fuel Readings",
	"cash-up": "Cash Up",
}


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


def _record_audit(site, actor, category, object_label, action, before=None, after=None):
	return SiteAuditEvent.objects.create(
		site=site,
		actor=actor,
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
	recent_entries = ShiftEntry.objects.select_related("site", "created_by")[:10]
	return render(request, "ledger/hq_dashboard.html", {"sites": sites, "recent_entries": recent_entries})


@login_required
def site_dashboard(request, slug):
	site = request.current_site
	selected_date, today = _selected_business_date(request)
	return render(request, "ledger/site_dashboard.html", {
		"site": site,
		"tank_count": site.tanks.count(),
		"speed_point_count": site.speed_points.count(),
		"selected_date": selected_date,
		"today": today,
		"site_forms": SITE_FORM_LABELS,
		"can_manage_users": _can_manage_site_users(request.user, site),
		"history_count": site.audit_events.count(),
	})


@login_required
def blank_site_form(request, slug, form_key):
	label = SITE_FORM_LABELS.get(form_key)
	if label is None:
		raise Http404
	selected_date, today = _selected_business_date(request)
	if form_key == "book-in-fuel":
		return _book_in_fuel(request, label, selected_date, today)
	if form_key == "daily-fuel-readings":
		return _daily_fuel_shift_selection(request, label, selected_date)
	return render(request, "ledger/blank_site_form.html", {
		"site": request.current_site,
		"form_label": label,
		"selected_date": selected_date,
	})


def _book_in_fuel(request, label, selected_date, today):
	site = request.current_site
	tanks = list(site.tanks.order_by("name"))
	bookings = {
		booking.tank_id: booking
		for booking in FuelBooking.objects.filter(site=site, business_date=selected_date)
	}
	form = BookInFuelForm(
		tanks,
		data=request.POST if request.method == "POST" else None,
		initial={"business_date": selected_date},
		initial_quantities={tank_id: booking.qty_litres for tank_id, booking in bookings.items()},
	)
	if request.method == "POST" and form.is_valid():
		quantities = [
			(tank, form.cleaned_data[field_name])
			for tank, field_name in form.tank_fields
			if form.cleaned_data[field_name] is not None
		]
		if not quantities:
			form.add_error(None, "Enter a booked-in quantity for at least one tank.")
		else:
			before = {
				str(tank_id): str(booking.qty_litres)
				for tank_id, booking in bookings.items()
			}
			with transaction.atomic():
				for tank, quantity in quantities:
					FuelBooking.objects.update_or_create(
						site=site,
						tank=tank,
						business_date=form.cleaned_data["business_date"],
						defaults={"qty_litres": quantity, "created_by": request.user},
					)
			_record_audit(
				site, request.user, "Book IN Fuel", selected_date.isoformat(), "saved",
				before=before,
				after={str(tank.pk): str(quantity) for tank, quantity in quantities},
			)
			messages.success(request, "Fuel book-in quantities saved.")
			return redirect(
				f"{reverse('blank-site-form', args=[site.slug, 'book-in-fuel'])}"
				f"?date={form.cleaned_data['business_date'].isoformat()}"
			)
	tank_rows = [(tank, form[field_name]) for tank, field_name in form.tank_fields]
	return render(request, "ledger/book_in_fuel.html", {
		"site": site,
		"form_label": label,
		"selected_date": selected_date,
		"today": today,
		"form": form,
		"tank_rows": tank_rows,
	})


def _daily_fuel_shift_selection(request, label, selected_date):
	site = request.current_site
	form = ShiftSelectionForm(
		site,
		data=request.POST if request.method == "POST" else None,
		initial={"business_date": selected_date},
	)
	if request.method == "POST" and form.is_valid():
		return redirect(
			f"{reverse('daily-fuel-reading-entry', args=[site.slug])}"
			f"?date={form.cleaned_data['business_date'].isoformat()}"
			f"&shift={form.cleaned_data['shift_label']}"
		)
	return render(request, "ledger/daily_fuel_shift.html", {
		"site": site,
		"form_label": label,
		"selected_date": selected_date,
		"form": form,
	})


@login_required
def daily_fuel_reading_entry(request, slug):
	site = request.current_site
	selected_date, _ = _selected_business_date(request)
	shift_label = request.POST.get("shift_label") or request.GET.get("shift", "")
	if shift_label not in site.get_shift_names():
		return redirect(
			f"{reverse('blank-site-form', args=[site.slug, 'daily-fuel-readings'])}"
			f"?date={selected_date.isoformat()}"
		)
	tanks = list(site.tanks.prefetch_related("nozzles").order_by("name"))
	reading = DailyFuelReading.objects.filter(
		site=site,
		business_date=selected_date,
		shift_label=shift_label,
	).first()
	initial_readings = {"dips": {}, "nozzles": {}}
	if reading:
		initial_readings["dips"] = {
			item.tank_id: item.dip_litres for item in reading.tank_readings.all()
		}
		initial_readings["nozzles"] = {
			item.nozzle_id: {
				"mechanical": item.mechanical_reading,
				"electronic": item.electronic_reading,
			}
			for item in reading.nozzle_readings.all()
		}
	form = DailyFuelReadingForm(
		site,
		request.POST if request.method == "POST" else None,
		initial={"business_date": selected_date, "shift_label": shift_label},
		initial_readings=initial_readings,
	)
	if request.method == "POST" and form.is_valid():
		if not tanks:
			form.add_error(None, "Configure at least one tank before entering readings.")
		else:
			before = _daily_reading_snapshot(reading) if reading else {}
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
				for tank, field_name in form.tank_fields:
					TankDipReading.objects.update_or_create(
						daily_reading=reading,
						tank=tank,
						defaults={"dip_litres": form.cleaned_data[field_name]},
					)
				for tank, nozzle, mechanical_name, electronic_name in form.nozzle_fields:
					NozzleFuelReading.objects.update_or_create(
						daily_reading=reading,
						nozzle=nozzle,
						defaults={
							"mechanical_reading": form.cleaned_data[mechanical_name],
							"electronic_reading": form.cleaned_data[electronic_name],
						},
					)
			after = _daily_reading_snapshot(reading)
			_record_audit(
				site,
				request.user,
				"Daily Fuel Readings",
				f"{selected_date.isoformat()} / {shift_label}",
				"created" if not before else "updated",
				before=before,
				after=after,
			)
			messages.success(request, "Daily fuel readings saved.")
			return redirect(
				f"{reverse('daily-fuel-reading-entry', args=[site.slug])}"
				f"?date={selected_date.isoformat()}&shift={shift_label}"
			)
	tank_rows = []
	for tank in tanks:
		nozzle_rows = []
		for nozzle in tank.nozzles.all():
			mechanical_name = f"nozzle_{nozzle.pk}_mechanical"
			electronic_name = f"nozzle_{nozzle.pk}_electronic"
			nozzle_rows.append((nozzle, form[mechanical_name], form[electronic_name]))
		tank_rows.append((tank, form[f"tank_{tank.pk}_dip"], nozzle_rows))
	return render(request, "ledger/daily_fuel_readings.html", {
		"site": site,
		"selected_date": selected_date,
		"shift_label": shift_label,
		"form": form,
		"tank_rows": tank_rows,
		"reading": reading,
	})


def _daily_reading_snapshot(reading):
	return {
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
	site = request.current_site
	form = SiteUserCreateForm(site, request.POST or None)
	if request.method == "POST" and form.is_valid():
		data = form.cleaned_data
		user = User.objects.create_user(
			username=data["username"],
			password=data["password"],
			first_name=data["first_name"],
			last_name=data["last_name"],
			email=data["email"],
		)
		UserProfile.objects.create(user=user, role=UserProfile.Role.SITE, site=site)
		for group in data["groups"]:
			group.users.add(user)
		_record_audit(site, request.user, "Site user", user.username, "created", after={"groups": [g.name for g in data["groups"]]})
		messages.success(request, f"Site user {user.username} created.")
		return redirect("site-users", slug=site.slug)
	return render(request, "ledger/site_user_form.html", {"site": site, "form": form})


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
			group.save()
			form.save_m2m()
			_record_audit(site, request.user, "Site group", group.name, "created", after={"users": list(group.users.values_list("username", flat=True))})
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
def site_history(request, slug):
	site = request.current_site
	events = site.audit_events.select_related("actor")
	return render(request, "ledger/site_history.html", {"site": site, "events": events})


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
