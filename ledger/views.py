from datetime import date

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User

from .forms import (
	BookInFuelForm,
	ElectronicReadingForm,
	MechanicalReadingForm,
	SiteGroupForm,
	SiteUserEditForm,
	TankReadingForm,
	SiteUserCreateForm,
)
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
from .models import SITE_FORM_PERMISSION_CHOICES


SITE_FORM_LABELS = {
	"book-in-fuel": "Book IN Fuel",
	"daily-fuel-readings": "Fuel Readings",
	"cash-up": "Cash Up",
}
FUEL_READING_FORM_LABELS = {
	"tank-reading": "Tank Reading",
	"mechanical-readings": "Mechanical Readings",
	"electronic-readings": "Electronic Readings",
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
	permissions = _site_form_permissions(user, site)
	if form_key == "daily-fuel-readings":
		return any(key in permissions for key in FUEL_READING_FORM_LABELS)
	return form_key in permissions


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
	recent_entries = ShiftEntry.objects.select_related("site", "created_by")[:10]
	return render(request, "ledger/hq_dashboard.html", {"sites": sites, "recent_entries": recent_entries})


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
			if key != "daily-fuel-readings" and key in permissions
		},
		"fuel_reading_forms": [
			{"key": key, "label": label}
			for key, label in FUEL_READING_FORM_LABELS.items()
			if key in permissions
		],
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
	if form_key == "daily-fuel-readings":
		return redirect(f"{reverse('site-dashboard', args=[site.slug])}?date={selected_date.isoformat()}&shift={shift_label}")
	return render(request, "ledger/blank_site_form.html", {
		"site": site,
		"form_key": form_key,
		"form_label": label,
		"selected_date": selected_date,
		"shift_label": shift_label,
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
		messages.success(request, "Fuel delivery quantities saved.")
		return redirect(
			f"{reverse('blank-site-form', args=[site.slug, 'book-in-fuel'])}"
			f"?date={form.cleaned_data['business_date'].isoformat()}&shift={shift_label}"
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
		reading_type_field = "mechanical_reading" if reading_type == "mechanical-readings" else "electronic_reading"
		initial_nozzle_readings = {
			nozzle_id: getattr(item, reading_type_field)
			for nozzle_id, item in existing_nozzle_readings.items()
			if getattr(item, reading_type_field) is not None
		}
	initial = {"business_date": selected_date, "shift_label": shift_label}
	form_class = {
		"tank-reading": TankReadingForm,
		"mechanical-readings": MechanicalReadingForm,
		"electronic-readings": ElectronicReadingForm,
	}[reading_type]
	form = form_class(
		site,
		data=request.POST if request.method == "POST" else None,
		initial=initial,
		initial_readings=initial_tank_readings if reading_type == "tank-reading" else initial_nozzle_readings,
	)
	if request.method == "POST" and form.is_valid():
		fields = form.tank_fields if reading_type == "tank-reading" else form.nozzle_fields
		if not fields:
			form.add_error(None, "Configure the relevant tanks or nozzles for this site before entering readings.")
		else:
			if reading_type == "tank-reading":
				before = {
					str(item.tank_id): {
						"dip_litres": str(item.dip_litres),
						"depth_cm": str(item.depth_cm) if item.depth_cm is not None else None,
					}
					for item in reading.tank_readings.all()
				} if reading else {}
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
			messages.success(request, f"{form_label} saved.")
			return redirect(
				f"{reverse('fuel-reading-entry', args=[site.slug, reading_type])}"
				f"?date={selected_date.isoformat()}&shift={shift_label}"
			)
	if reading_type == "tank-reading":
		field_rows = [
			(tank, form[dip_field_name], form[depth_field_name], form[capacity_field_name])
			for tank, dip_field_name, depth_field_name, capacity_field_name in form.tank_fields
		]
	else:
		field_rows = [(tank, nozzle, form[field_name]) for tank, nozzle, field_name in form.nozzle_fields]
	return render(request, "ledger/fuel_reading_form.html", {
		"site": site,
		"selected_date": selected_date,
		"shift_label": shift_label,
		"reading_type": reading_type,
		"form_label": form_label,
		"form": form,
		"field_rows": field_rows,
		"is_tank_reading": reading_type == "tank-reading",
		"history_key": reading_type,
		"fuel_reading_forms": [
			{"key": key, "label": label}
			for key, label in FUEL_READING_FORM_LABELS.items()
		],
		"active_reading_type": reading_type,
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


def _site_user_snapshot(user):
	profile = user.fuel_profile
	return {
		"username": user.username,
		"name": user.get_full_name(),
		"email": user.email,
		"is_active": user.is_active,
		"groups": list(user.fuel_site_groups.order_by("name").values_list("name", flat=True)),
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
		profile = UserProfile.objects.create(
			user=user,
			role=UserProfile.Role.SITE,
			site=site,
			form_permissions=data["form_permissions"],
			can_configure=data["can_configure"],
			can_manage_users=data["can_manage_users"],
		)
		for group in data["groups"]:
			group.users.add(user)
		_record_audit(site, request.user, "Site user", user.username, "created", after=_site_user_snapshot(user))
		messages.success(request, f"Site user {user.username} created.")
		return redirect("site-users", slug=site.slug)
	return render(request, "ledger/site_user_form.html", {"site": site, "form": form})


@login_required
def site_user_edit(request, slug, user_id):
	denied = _require_site_user_manager(request)
	if denied:
		return denied
	site = request.current_site
	user = get_object_or_404(User.objects.select_related("fuel_profile"), pk=user_id, fuel_profile__site=site)
	form = SiteUserEditForm(site, user, request.POST or None)
	if request.method == "POST" and form.is_valid():
		data = form.cleaned_data
		before = _site_user_snapshot(user)
		user.username = data["username"]
		user.first_name = data["first_name"]
		user.last_name = data["last_name"]
		user.email = data["email"]
		user.is_active = data["is_active"]
		if data["password"]:
			user.set_password(data["password"])
		user.save()
		profile = user.fuel_profile
		profile.form_permissions = data["form_permissions"]
		profile.can_configure = data["can_configure"]
		profile.can_manage_users = data["can_manage_users"]
		profile.save(update_fields=["form_permissions", "can_configure", "can_manage_users"])
		user.fuel_site_groups.set(data["groups"])
		_record_audit(site, request.user, "Site user", user.username, "changed", before=before, after=_site_user_snapshot(user))
		messages.success(request, f"Site user {user.username} updated.")
		return redirect("site-users", slug=site.slug)
	return render(request, "ledger/site_user_form.html", {
		"site": site,
		"form": form,
		"account": user,
		"edit_mode": True,
	})


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
	return render(request, "ledger/site_history.html", {"site": site, "events": events})


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
		"form_label": form_label,
		"selected_date": selected_date,
		"shift_label": shift_label,
		"form_url": form_url,
	})


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
