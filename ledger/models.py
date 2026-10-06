from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify


class Site(models.Model):
	SHIFT_COUNT_CHOICES = [
		(1, "1"),
		(2, "2"),
		(3, "3"),
	]

	name = models.CharField(max_length=120, unique=True, verbose_name="Site Name")
	slug = models.SlugField(max_length=140, unique=True, blank=True)
	address = models.TextField(blank=True, verbose_name="Address")
	contact_number = models.CharField(max_length=30, blank=True, verbose_name="Contact Number")
	contact_email = models.EmailField(blank=True)
	manager_name = models.CharField(max_length=120, blank=True)
	shift_count = models.PositiveSmallIntegerField(
		choices=SHIFT_COUNT_CHOICES,
		default=1,
		verbose_name="Amount of shifts",
	)
	is_active = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["name"]

	def save(self, *args, **kwargs):
		if not self.slug:
			self.slug = slugify(self.name)
		super().save(*args, **kwargs)

	def __str__(self):
		return self.name

	def get_shift_names(self):
		return {
			1: ("Day",),
			2: ("Day", "Night"),
			3: ("Morning", "Afternoon", "Evening"),
		}[self.shift_count]


class Tank(models.Model):
	site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="tanks")
	name = models.CharField(max_length=70, help_text="For example: Tank 1")
	product = models.CharField(max_length=90, help_text="For example: Diesel")

	class Meta:
		ordering = ["name"]
		constraints = [
			models.UniqueConstraint(fields=["site", "name"], name="unique_tank_name_per_site"),
		]

	def __str__(self):
		display_name = self.name if self.name.casefold().startswith("tank ") else f"Tank {self.name}"
		return f"{display_name} - {self.product}"


class Nozzle(models.Model):
	tank = models.ForeignKey(Tank, on_delete=models.CASCADE, related_name="nozzles")
	name = models.CharField(max_length=60, help_text="For example: Nozzle 1")

	class Meta:
		ordering = ["name"]
		constraints = [
			models.UniqueConstraint(fields=["tank", "name"], name="unique_nozzle_name_per_tank"),
		]

	def __str__(self):
		return self.name


class SpeedPoint(models.Model):
	site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="speed_points")
	point_id = models.CharField(max_length=10, verbose_name="Speed point no/ID")
	serial_number = models.CharField(max_length=30, verbose_name="Speed point Serial No")

	class Meta:
		ordering = ["point_id"]
		constraints = [
			models.UniqueConstraint(fields=["site", "point_id"], name="unique_speed_point_id_per_site"),
			models.UniqueConstraint(fields=["site", "serial_number"], name="unique_speed_point_serial_per_site"),
		]

	def __str__(self):
		return self.point_id


class FuelBooking(models.Model):
	site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="fuel_bookings")
	tank = models.ForeignKey(Tank, on_delete=models.PROTECT, related_name="fuel_bookings")
	business_date = models.DateField()
	qty_litres = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Qty L booked in")
	created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fuel_bookings")
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-business_date", "tank__name"]
		constraints = [
			models.UniqueConstraint(fields=["site", "tank", "business_date"], name="unique_fuel_booking_per_site_tank_date"),
		]

	def clean(self):
		if self.site_id and self.tank_id and self.tank.site_id != self.site_id:
			raise ValidationError({"tank": "The selected tank must belong to this site."})

	def __str__(self):
		return f"{self.site} / {self.tank} / {self.business_date}"


class UserProfile(models.Model):
	class Role(models.TextChoices):
		HQ = "HQ", "HQ"
		SITE = "SITE", "Site"

	user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="fuel_profile")
	role = models.CharField(max_length=4, choices=Role.choices, default=Role.SITE)
	site = models.ForeignKey(Site, on_delete=models.PROTECT, null=True, blank=True, related_name="users")
	can_configure = models.BooleanField(default=False, verbose_name="Can configure cash-up fields")
	can_manage_users = models.BooleanField(default=False, verbose_name="Can manage site users and groups")

	def clean(self):
		if self.role == self.Role.SITE and self.site_id is None:
			raise ValidationError({"site": "A site user must be assigned to a site."})
		if self.role == self.Role.HQ and self.site_id is not None:
			raise ValidationError({"site": "HQ users are not assigned to a single site."})

	def __str__(self):
		return f"{self.user} ({self.get_role_display()})"


class CashUpForm(models.Model):
	site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="cash_up_form")
	name = models.CharField(max_length=120, default="Shift cash-up")
	version = models.PositiveIntegerField(default=1)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["site__name"]

	def __str__(self):
		return f"{self.site}: {self.name}"


class CashUpField(models.Model):
	class FieldType(models.TextChoices):
		TEXT = "text", "Short text"
		NUMBER = "number", "Number"
		MONEY = "money", "Money"
		SELECT = "select", "Choice list"
		CHECKBOX = "checkbox", "Yes / no"
		CALCULATED = "calculated", "Calculated"

	form = models.ForeignKey(CashUpForm, on_delete=models.CASCADE, related_name="fields")
	section = models.CharField(max_length=80, default="Cash-up")
	label = models.CharField(max_length=120)
	key = models.SlugField(max_length=140)
	field_type = models.CharField(max_length=12, choices=FieldType.choices, default=FieldType.MONEY)
	required = models.BooleanField(default=False)
	choices = models.JSONField(default=list, blank=True)
	formula = models.CharField(max_length=300, blank=True)
	position = models.PositiveSmallIntegerField(default=0)

	class Meta:
		ordering = ["section", "position", "id"]
		constraints = [
			models.UniqueConstraint(fields=["form", "key"], name="unique_cashup_field_key_per_form"),
		]

	def clean(self):
		if self.field_type == self.FieldType.CALCULATED and not self.formula:
			raise ValidationError({"formula": "Calculated fields need a formula."})
		if self.field_type == self.FieldType.SELECT and not self.choices:
			raise ValidationError({"choices": "Choice lists need at least one option."})
		if self.field_type != self.FieldType.CALCULATED and self.formula:
			raise ValidationError({"formula": "Only calculated fields can have a formula."})

	def save(self, *args, **kwargs):
		if not self.key:
			base_key = slugify(self.label).replace("-", "_") or "field"
			self.key = base_key
			suffix = 2
			while CashUpField.objects.filter(form=self.form, key=self.key).exclude(pk=self.pk).exists():
				self.key = f"{base_key}_{suffix}"
				suffix += 1
		super().save(*args, **kwargs)

	def __str__(self):
		return self.label


class ShiftEntry(models.Model):
	class Status(models.TextChoices):
		SUBMITTED = "submitted", "Submitted"

	site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="shift_entries")
	form = models.ForeignKey(CashUpForm, on_delete=models.PROTECT, related_name="entries")
	business_date = models.DateField()
	shift_label = models.CharField(max_length=60)
	created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fuel_shift_entries")
	status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED)
	values = models.JSONField(default=dict)
	schema_snapshot = models.JSONField(default=list)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-business_date", "-created_at"]
		constraints = [
			models.UniqueConstraint(fields=["site", "business_date", "shift_label"], name="unique_site_date_shift_label"),
		]

	def clean(self):
		if self.site_id and self.form_id and self.site_id != self.form.site_id:
			raise ValidationError({"form": "The cash-up form must belong to the selected site."})

	def __str__(self):
		return f"{self.site} / {self.business_date} / {self.shift_label}"


class DailyFuelReading(models.Model):
	site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="daily_fuel_readings")
	business_date = models.DateField()
	shift_label = models.CharField(max_length=30)
	created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="daily_fuel_readings_created")
	updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="daily_fuel_readings_updated")
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-business_date", "shift_label"]
		constraints = [
			models.UniqueConstraint(fields=["site", "business_date", "shift_label"], name="unique_daily_fuel_reading_per_site_shift"),
		]

	def clean(self):
		if self.site_id and self.shift_label not in self.site.get_shift_names():
			raise ValidationError({"shift_label": "Select a shift configured for this site."})

	def __str__(self):
		return f"{self.site} / {self.business_date} / {self.shift_label}"


class TankDipReading(models.Model):
	daily_reading = models.ForeignKey(DailyFuelReading, on_delete=models.CASCADE, related_name="tank_readings")
	tank = models.ForeignKey(Tank, on_delete=models.PROTECT, related_name="dip_readings")
	dip_litres = models.DecimalField(max_digits=12, decimal_places=2)

	class Meta:
		constraints = [
			models.UniqueConstraint(fields=["daily_reading", "tank"], name="unique_tank_dip_per_daily_reading"),
		]


class NozzleFuelReading(models.Model):
	daily_reading = models.ForeignKey(DailyFuelReading, on_delete=models.CASCADE, related_name="nozzle_readings")
	nozzle = models.ForeignKey(Nozzle, on_delete=models.PROTECT, related_name="daily_readings")
	mechanical_reading = models.DecimalField(max_digits=14, decimal_places=2)
	electronic_reading = models.DecimalField(max_digits=14, decimal_places=2)

	class Meta:
		constraints = [
			models.UniqueConstraint(fields=["daily_reading", "nozzle"], name="unique_nozzle_reading_per_daily_reading"),
		]


class SiteGroup(models.Model):
	site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="groups")
	name = models.CharField(max_length=100)
	users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="fuel_site_groups")
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["name"]
		constraints = [
			models.UniqueConstraint(fields=["site", "name"], name="unique_site_group_name"),
		]

	def __str__(self):
		return f"{self.site}: {self.name}"


class SiteAuditEvent(models.Model):
	site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="audit_events")
	actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="fuel_audit_events")
	category = models.CharField(max_length=40)
	object_label = models.CharField(max_length=160)
	action = models.CharField(max_length=20)
	before = models.JSONField(default=dict, blank=True)
	after = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-created_at"]

	def __str__(self):
		return f"{self.action}: {self.object_label}"
