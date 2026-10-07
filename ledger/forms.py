import hashlib
import re
from decimal import Decimal

from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth.models import Permission, User
from django.utils import timezone

from .models import CashUpAdditional, FuelBooking, ManualShopPoint, SITE_FORM_PERMISSION_CHOICES, SiteGroup, SpeedPoint, Tank


def _natural_name_key(value):
	return tuple(
		(1, int(part)) if part.isdigit() else (0, part.casefold())
		for part in re.split(r"(\d+)", value)
	)


class BookInFuelForm(forms.Form):
	business_date = forms.DateField(widget=forms.HiddenInput)
	shift_label = forms.ChoiceField(widget=forms.HiddenInput)
	delivery_count = forms.IntegerField(
		min_value=1,
		max_value=FuelBooking.MAX_DELIVERIES_PER_SHIFT,
		widget=forms.HiddenInput,
	)

	def __init__(self, tanks, site, *args, initial_deliveries=None, **kwargs):
		super().__init__(*args, **kwargs)
		self.tanks = list(tanks)
		self.fields["shift_label"].choices = [(label, label) for label in site.get_shift_names()]
		initial_deliveries = initial_deliveries or {}
		self.delivery_fields = []
		for delivery_number in range(1, FuelBooking.MAX_DELIVERIES_PER_SHIFT + 1):
			initial_delivery = initial_deliveries.get(delivery_number, {})
			truck_field = f"delivery_{delivery_number}_truck_reference"
			self.fields[truck_field] = forms.CharField(
				label="Truck / delivery reference",
				required=False,
				max_length=50,
				initial=initial_delivery.get("truck_reference", ""),
			)
			tank_fields = []
			for tank in self.tanks:
				field_name = f"delivery_{delivery_number}_tank_{tank.pk}_qty_litres"
				self.fields[field_name] = forms.DecimalField(
					label="Qty L booked in",
					required=False,
					min_value=0,
					max_digits=12,
					decimal_places=2,
					initial=initial_delivery.get("quantities", {}).get(tank.pk),
					widget=forms.NumberInput(attrs={"class": "quantity-input", "min": "0", "step": "0.01"}),
				)
				tank_fields.append((tank, field_name))
			self.delivery_fields.append((delivery_number, truck_field, tank_fields))

	def clean(self):
		cleaned_data = super().clean()
		delivery_count = cleaned_data.get("delivery_count")
		if delivery_count:
			has_quantities = any(
				cleaned_data.get(field_name) is not None
				for delivery_number, _, tank_fields in self.delivery_fields
				if delivery_number <= delivery_count
				for _, field_name in tank_fields
			)
			if not has_quantities:
				self.add_error(None, "Enter a quantity for at least one tank in a delivery.")
		return cleaned_data

	def clean_business_date(self):
		business_date = self.cleaned_data["business_date"]
		if business_date > timezone.localdate():
			raise forms.ValidationError("Book-in dates cannot be in the future.")
		return business_date


class FuelTransferForm(forms.Form):
	business_date = forms.DateField(widget=forms.HiddenInput)
	shift_label = forms.CharField(widget=forms.HiddenInput)
	from_tank = forms.ModelChoiceField(queryset=Tank.objects.none(), label="From tank")
	to_tank = forms.ModelChoiceField(queryset=Tank.objects.none(), label="To tank")
	amount_litres = forms.DecimalField(
		label="Amount (L)",
		min_value=0.01,
		max_value=100000000,
		max_digits=11,
		decimal_places=2,
		widget=forms.NumberInput(attrs={"min": "0.01", "max": "100000000", "step": "0.01"}),
	)

	def __init__(self, site, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.site = site
		tanks = Tank.objects.filter(site=site).order_by("name")
		self.fields["from_tank"].queryset = tanks
		self.fields["to_tank"].queryset = tanks

	def clean_business_date(self):
		business_date = self.cleaned_data["business_date"]
		if business_date > timezone.localdate():
			raise forms.ValidationError("Transfer dates cannot be in the future.")
		return business_date

	def clean_shift_label(self):
		shift_label = self.cleaned_data["shift_label"]
		if shift_label not in self.site.get_shift_names():
			raise forms.ValidationError("Select a shift configured for this site.")
		return shift_label

	def clean(self):
		cleaned_data = super().clean()
		from_tank = cleaned_data.get("from_tank")
		to_tank = cleaned_data.get("to_tank")
		if from_tank and to_tank and from_tank.pk == to_tank.pk:
			self.add_error("to_tank", "Choose a different destination tank.")
		return cleaned_data


class ShiftSelectionForm(forms.Form):
	business_date = forms.DateField(widget=forms.HiddenInput)
	shift_label = forms.ChoiceField(label="Shift")

	def __init__(self, site, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.fields["shift_label"].choices = [(label, label) for label in site.get_shift_names()]

	def clean_business_date(self):
		business_date = self.cleaned_data["business_date"]
		if business_date > timezone.localdate():
			raise forms.ValidationError("Reading dates cannot be in the future.")
		return business_date


class SiteReadingForm(forms.Form):
	business_date = forms.DateField(widget=forms.HiddenInput)
	shift_label = forms.CharField(widget=forms.HiddenInput)

	def __init__(self, site, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.site = site

	def clean_business_date(self):
		business_date = self.cleaned_data["business_date"]
		if business_date > timezone.localdate():
			raise forms.ValidationError("Reading dates cannot be in the future.")
		return business_date

	def clean_shift_label(self):
		shift_label = self.cleaned_data["shift_label"]
		if shift_label not in self.site.get_shift_names():
			raise forms.ValidationError("Select a shift configured for this site.")
		return shift_label


class CashUpEntryForm(forms.Form):
	MAX_REPEAT_ROWS = 50
	business_date = forms.DateField(widget=forms.HiddenInput)
	shift_label = forms.CharField(widget=forms.HiddenInput)

	def __init__(self, site, section, *args, initial_values=None, **kwargs):
		super().__init__(*args, **kwargs)
		self.site = site
		self.section = section
		self.entry_fields = []
		self.repeat_groups = []
		initial_values = initial_values or {}
		if section == "cash":
			self._add_money_field("coins", "Coins", initial_values)
			self._add_money_field("safedrops", "Safedrops", initial_values)
		elif section == "vouchers":
			self._add_repeating_money_fields("vouchers", "Voucher", "voucher", "voucher_count", initial_values, "vouchers")
		elif section == "airtime":
			self._add_text_field("airtime_shift_nr", "Airtime Shift Number", initial_values, max_length=10)
			self._add_money_field("airtime_slip", "Airtime Slip", initial_values)
			self._add_money_field("airtime_system", "Airtime System", initial_values)
			self._add_money_field("airtime_balance", "Airtime Balance", initial_values)
			self._add_money_field("airtime_loaded", "Airtime Loaded", initial_values, total_group="airtime-loaded")
		elif section == "sales-account":
			self._add_repeating_money_fields("sales_account", "Account", "sales_account", "sales_account_count", initial_values)
			self._add_repeating_money_fields("arrears_account", "Arrears Account", "arrears_account", "arrears_account_count", initial_values)
		elif section == "fuel":
			product_groups = {}
			for tank in Tank.objects.filter(site=site).order_by("name"):
				product = " ".join(tank.product.split())
				normalized_product = product.casefold()
				product_groups.setdefault(normalized_product, {"label": product, "tanks": []})["tanks"].append(tank)
			for normalized_product, product_group in product_groups.items():
				key_hash = hashlib.sha256(normalized_product.encode("utf-8")).hexdigest()[:16]
				key = f"fuel_product_{key_hash}"
				initial_value = initial_values.get(key)
				if initial_value is None:
					initial_value = sum((
						Decimal(str(initial_values.get(f"fuel_{tank.pk}") or 0))
						for tank in product_group["tanks"]
					), Decimal("0"))
				self._add_money_field(key, product_group["label"], {key: initial_value}, total_group="fuel")
		elif section == "payouts":
			self._add_repeating_money_fields("payouts", "Payout", "payout", "payout_count", initial_values, "payout")
		elif section == "eft-driveway":
			for point in SpeedPoint.objects.filter(site=site):
				key = f"eft_{point.pk}"
				self._add_money_field(key, f"EFT/Driveway {point.serial_number}", initial_values)
		elif section == "manual-shop":
			for point in ManualShopPoint.objects.filter(site=site):
				key = f"manual_{point.pk}"
				self._add_money_field(key, f"Manual/Shop {point.serial_number}", initial_values)
		elif section == "cash-up-additional":
			for additional in CashUpAdditional.objects.filter(site=site):
				key = f"additional_{additional.pk}"
				self._add_money_field(key, additional.additional_field, initial_values)

	def _add_money_field(self, key, label, initial_values, total_group=None):
		widget_attrs = {"min": "0", "max": "100000000", "step": "0.01"}
		if total_group:
			widget_attrs["data-cash-up-total-input"] = total_group
		elif self.section in {"cash", "eft-driveway", "manual-shop", "cash-up-additional"}:
			widget_attrs["data-cash-up-total-input"] = self.section
		self.fields[key] = forms.DecimalField(
			label=label,
			required=False,
			min_value=0,
			max_value=100000000,
			max_digits=11,
			decimal_places=2,
			initial=initial_values.get(key),
			widget=forms.NumberInput(attrs=widget_attrs),
		)
		self.entry_fields.append((key, label))

	def _add_repeating_money_fields(self, group_key, label, field_prefix, count_key, initial_values, legacy_key=None):
		matching_indices = []
		prefix = f"{field_prefix}_"
		for key in initial_values:
			if key.startswith(prefix) and key[len(prefix):].isdigit():
				matching_indices.append(int(key[len(prefix):]))
		repeat_counts = initial_values.get("repeat_counts", {})
		default_count = max(matching_indices + [int(repeat_counts.get(group_key, 1))], default=1)
		if self.is_bound:
			try:
				row_count = int(self.data.get(count_key, default_count))
			except (TypeError, ValueError):
				row_count = default_count
			row_count = min(max(row_count, 1), self.MAX_REPEAT_ROWS)
		else:
			row_count = min(max(default_count, 1), self.MAX_REPEAT_ROWS)
		self.fields[count_key] = forms.IntegerField(
			min_value=1,
			max_value=self.MAX_REPEAT_ROWS,
			initial=row_count,
			widget=forms.HiddenInput(attrs={"data-repeat-count": group_key}),
		)
		rows = []
		for index in range(1, row_count + 1):
			key = f"{field_prefix}_{index}"
			initial = initial_values.get(key)
			if index == 1 and initial is None and legacy_key:
				initial = initial_values.get(legacy_key)
			self._add_money_field(key, f"{label} {index}", {key: initial}, total_group=group_key)
			rows.append((self.fields[key].label, key))
		self.repeat_groups.append({
			"key": group_key,
			"label": label,
			"count_key": count_key,
			"rows": [(row_label, self[key]) for row_label, key in rows],
			"count": row_count,
		})

	def _add_text_field(self, key, label, initial_values, max_length=60):
		self.fields[key] = forms.CharField(
			label=label,
			required=False,
			max_length=max_length,
			initial=initial_values.get(key, ""),
			widget=forms.TextInput(attrs={"maxlength": max_length, "class": "airtime-shift-number-input"}),
		)
		self.entry_fields.append((key, label))

	def clean_business_date(self):
		business_date = self.cleaned_data["business_date"]
		if business_date > timezone.localdate():
			raise forms.ValidationError("Cash-up dates cannot be in the future.")
		return business_date

	def clean_shift_label(self):
		shift_label = self.cleaned_data["shift_label"]
		if shift_label not in self.site.get_shift_names():
			raise forms.ValidationError("Select a shift configured for this site.")
		return shift_label


class TankReadingForm(SiteReadingForm):
	def __init__(self, site, *args, initial_readings=None, **kwargs):
		super().__init__(site, *args, **kwargs)
		initial_readings = initial_readings or {}
		self.tank_fields = []
		for tank in site.tanks.order_by("name"):
			dip_field_name = f"tank_{tank.pk}_dip"
			depth_field_name = f"tank_{tank.pk}_depth_cm"
			capacity_field_name = f"tank_{tank.pk}_capacity_litres"
			previous = initial_readings.get(tank.pk, {})
			self.fields[dip_field_name] = forms.DecimalField(
				label="Dip reading (L)",
				min_value=0,
				max_digits=12,
				decimal_places=2,
				initial=previous.get("dip_litres"),
				widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
			)
			self.fields[depth_field_name] = forms.DecimalField(
				label="Depth (cm)",
				required=False,
				min_value=0,
				max_digits=8,
				decimal_places=2,
				initial=previous.get("depth_cm"),
				widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
			)
			self.fields[capacity_field_name] = forms.CharField(
				label="Tank capacity (L)",
				required=False,
				disabled=True,
				initial=tank.capacity_litres,
			)
			self.tank_fields.append((tank, dip_field_name, depth_field_name, capacity_field_name))


class NozzleReadingForm(SiteReadingForm):
	reading_type = ""
	reading_label = ""

	def __init__(self, site, *args, initial_readings=None, **kwargs):
		super().__init__(site, *args, **kwargs)
		initial_readings = initial_readings or {}
		nozzles = [
			(tank, nozzle)
			for tank in site.tanks.prefetch_related("nozzles").all()
			for nozzle in tank.nozzles.all()
		]
		nozzles.sort(key=lambda row: (_natural_name_key(row[1].name), _natural_name_key(row[0].name)))
		self.nozzle_fields = []
		for tank, nozzle in nozzles:
			field_name = f"nozzle_{nozzle.pk}_{self.reading_type}"
			self.fields[field_name] = forms.DecimalField(
				label=self.reading_label,
				min_value=0,
				max_digits=14,
				decimal_places=2,
				initial=initial_readings.get(nozzle.pk),
				widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
			)
			self.nozzle_fields.append((tank, nozzle, field_name))


class MechanicalReadingForm(NozzleReadingForm):
	reading_type = "mechanical"
	reading_label = "Mechanical reading"


class ElectronicReadingForm(NozzleReadingForm):
	reading_type = "electronic"
	reading_label = "Electronic reading"


class SiteUserCreateForm(forms.Form):
	username = forms.CharField(max_length=150)
	first_name = forms.CharField(max_length=150, required=False)
	last_name = forms.CharField(max_length=150, required=False)
	email = forms.EmailField(required=False)
	password = forms.CharField(widget=forms.PasswordInput)
	confirm_password = forms.CharField(label="Confirm password", widget=forms.PasswordInput)
	phone_number = forms.CharField(max_length=30, required=False, label="Phone number")
	groups = forms.ModelMultipleChoiceField(
		queryset=SiteGroup.objects.none(),
		required=False,
		widget=FilteredSelectMultiple("site groups", False),
	)
	user_permissions = forms.ModelMultipleChoiceField(
		queryset=Permission.objects.none(),
		required=False,
		widget=FilteredSelectMultiple("user permissions", False),
	)
	form_permissions = forms.MultipleChoiceField(
		choices=SITE_FORM_PERMISSION_CHOICES,
		label="Site form permissions",
		required=False,
		widget=FilteredSelectMultiple("site permissions", False),
	)
	can_configure = forms.BooleanField(required=False, label="Can configure cash-up fields")
	can_manage_users = forms.BooleanField(required=False, label="Can manage site users and groups")

	def __init__(self, site, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.site = site
		self.fields["groups"].queryset = SiteGroup.objects.filter(site=site).order_by("name")
		self.fields["user_permissions"].queryset = Permission.objects.order_by("content_type__app_label", "content_type__model", "codename")
		self.fields["form_permissions"].initial = [permission for permission, _ in SITE_FORM_PERMISSION_CHOICES]

	def clean_username(self):
		username = self.cleaned_data["username"]
		if User.objects.filter(username=username).exists():
			raise forms.ValidationError("This username is already in use.")
		return username

	def clean(self):
		cleaned = super().clean()
		password = cleaned.get("password")
		confirm_password = cleaned.get("confirm_password")
		if password and confirm_password and password != confirm_password:
			self.add_error("confirm_password", "Passwords do not match.")
		return cleaned


class SiteGroupForm(forms.ModelForm):
	users = forms.ModelMultipleChoiceField(
		queryset=User.objects.none(),
		required=False,
		widget=FilteredSelectMultiple("site users", False),
	)
	form_permissions = forms.MultipleChoiceField(
		choices=SITE_FORM_PERMISSION_CHOICES,
		label="Granted site form permissions",
		required=False,
		widget=FilteredSelectMultiple("site permissions", False),
	)

	class Meta:
		model = SiteGroup
		fields = ("name", "users")

	def __init__(self, site, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.site = site
		self.fields["users"].queryset = User.objects.filter(fuel_profile__site=site).order_by("username")
		self.fields["form_permissions"].initial = self.instance.form_permissions

	def clean_name(self):
		name = self.cleaned_data["name"].strip()
		if SiteGroup.objects.filter(site=self.site, name__iexact=name).exclude(pk=self.instance.pk).exists():
			raise forms.ValidationError("A group with this name already exists at this site.")
		return name


class SiteUserEditForm(forms.Form):
	username = forms.CharField(max_length=150)
	first_name = forms.CharField(max_length=150, required=False)
	last_name = forms.CharField(max_length=150, required=False)
	email = forms.EmailField(required=False)
	phone_number = forms.CharField(max_length=30, required=False, label="Phone number")
	is_active = forms.BooleanField(required=False)
	groups = forms.ModelMultipleChoiceField(
		queryset=SiteGroup.objects.none(),
		required=False,
		widget=FilteredSelectMultiple("site groups", False),
	)
	user_permissions = forms.ModelMultipleChoiceField(
		queryset=Permission.objects.none(),
		required=False,
		widget=FilteredSelectMultiple("user permissions", False),
	)
	form_permissions = forms.MultipleChoiceField(
		choices=SITE_FORM_PERMISSION_CHOICES,
		label="Site form permissions",
		required=False,
		widget=FilteredSelectMultiple("site permissions", False),
	)
	can_configure = forms.BooleanField(required=False, label="Can configure cash-up fields")
	can_manage_users = forms.BooleanField(required=False, label="Can manage site users and groups")
	password = forms.CharField(required=False, widget=forms.PasswordInput, help_text="Leave blank to keep the current password.")
	confirm_password = forms.CharField(required=False, label="Confirm new password", widget=forms.PasswordInput)

	def __init__(self, site, user, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.site = site
		self.user = user
		profile = user.fuel_profile
		self.fields["groups"].queryset = SiteGroup.objects.filter(site=site).order_by("name")
		self.fields["user_permissions"].queryset = Permission.objects.order_by("content_type__app_label", "content_type__model", "codename")
		self.initial.setdefault("username", user.username)
		self.initial.setdefault("first_name", user.first_name)
		self.initial.setdefault("last_name", user.last_name)
		self.initial.setdefault("email", user.email)
		self.initial.setdefault("phone_number", profile.phone_number)
		self.initial.setdefault("is_active", user.is_active)
		self.initial.setdefault("groups", user.fuel_site_groups.filter(site=site))
		self.initial.setdefault("user_permissions", user.user_permissions.all())
		self.initial.setdefault("form_permissions", profile.form_permissions)
		self.initial.setdefault("can_configure", profile.can_configure)
		self.initial.setdefault("can_manage_users", profile.can_manage_users)

	def clean_username(self):
		username = self.cleaned_data["username"]
		if User.objects.filter(username=username).exclude(pk=self.user.pk).exists():
			raise forms.ValidationError("This username is already in use.")
		return username

	def clean(self):
		cleaned = super().clean()
		password = cleaned.get("password")
		confirm_password = cleaned.get("confirm_password")
		if password and password != confirm_password:
			self.add_error("confirm_password", "Passwords do not match.")
		elif confirm_password and not password:
			self.add_error("password", "Enter a new password to confirm it.")
		return cleaned