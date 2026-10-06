from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
import nested_admin

from .models import (
	DailyFuelReading,
	CashUpAdditional,
	FuelBooking,
	ManualShopPoint,
	Nozzle,
	NozzleFuelReading,
	Site,
	SiteAuditEvent,
	SiteGroup,
	SpeedPoint,
	Tank,
	TankDipReading,
	UserProfile,
	SITE_FORM_PERMISSION_CHOICES,
)


def _record_admin_audit(site, user, category, label, action, before=None, after=None):
	SiteAuditEvent.objects.create(
		site=site,
		actor=user,
		origin=SiteAuditEvent.Origin.HQ,
		category=category,
		object_label=label,
		action=action,
		before=before or {},
		after=after or {},
	)


def _record_site_inline_changes(request, site, formset):
	category = formset.model._meta.verbose_name.title()
	for obj in getattr(formset, "new_objects", []):
		_record_admin_audit(site, request.user, category, str(obj), "created", after={"record": str(obj)})
	for obj, changed_fields in getattr(formset, "changed_objects", []):
		inline_form = next((item for item in formset.forms if item.instance.pk == obj.pk), None)
		before = {}
		after = {}
		if inline_form:
			before = {name: str(inline_form.initial.get(name, "")) for name in changed_fields}
			after = {name: str(inline_form.cleaned_data.get(name, "")) for name in changed_fields}
		_record_admin_audit(site, request.user, category, str(obj), "changed", before, after)
	for obj in getattr(formset, "deleted_objects", []):
		_record_admin_audit(site, request.user, category, str(obj), "deleted", before={"record": str(obj)})
	for inline_form in getattr(formset, "forms", []):
		nested = getattr(inline_form, "nested_formsets", [])
		if isinstance(nested, dict):
			nested = nested.values()
		for nested_formset in nested:
			_record_site_inline_changes(request, site, nested_formset)


class TankInlineForm(forms.ModelForm):
	class Meta:
		model = Tank
		fields = ("name", "product", "capacity_litres")
		widgets = {
			"name": forms.TextInput(attrs={"size": 14, "style": "width: 145px; max-width: 100%;"}),
			"product": forms.TextInput(attrs={"size": 17, "style": "width: 170px; max-width: 100%;"}),
			"capacity_litres": forms.NumberInput(attrs={"min": "0", "step": "0.01", "style": "width: 120px; max-width: 100%;"}),
		}


class NozzleInlineForm(forms.ModelForm):
	name = forms.CharField(
		label="No",
		max_length=60,
		help_text="",
		widget=forms.TextInput(attrs={"size": 3, "style": "width: 45px; max-width: 100%;"}),
	)

	class Meta:
		model = Nozzle
		fields = ("name",)


class SpeedPointInlineForm(forms.ModelForm):
	class Meta:
		model = SpeedPoint
		fields = ("point_id", "serial_number")
		widgets = {
			"point_id": forms.TextInput(attrs={"size": 10, "style": "width: 10ch; max-width: 100%;"}),
			"serial_number": forms.TextInput(attrs={"size": 30, "style": "width: 30ch; max-width: 100%;"}),
		}


class UserProfileAdminForm(forms.ModelForm):
	form_permissions = forms.MultipleChoiceField(
		choices=SITE_FORM_PERMISSION_CHOICES,
		label="Site form permissions",
		required=False,
		widget=FilteredSelectMultiple("site form permissions", False),
	)

	class Meta:
		model = UserProfile
		fields = "__all__"


class UserProfileInline(admin.StackedInline):
	model = UserProfile
	form = UserProfileAdminForm
	extra = 0
	max_num = 1
	fields = ("role", "site", "form_permissions", "can_configure", "can_manage_users")
	verbose_name = "FuelLedger access"
	verbose_name_plural = "FuelLedger permissions"


class FuelLedgerUserAdmin(UserAdmin):
	inlines = (UserProfileInline,)

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		try:
			profile = form.instance.fuel_profile
		except UserProfile.DoesNotExist:
			return
		if profile.site_id:
			_record_admin_audit(
				profile.site,
				request.user,
				"Site user",
				form.instance.username,
				"changed" if change else "created",
				after={
					"role": profile.role,
					"form_permissions": profile.form_permissions,
					"can_configure": profile.can_configure,
					"can_manage_users": profile.can_manage_users,
				},
			)


class NozzleInline(nested_admin.NestedTabularInline):
	model = Nozzle
	form = NozzleInlineForm
	extra = 0
	fields = ("name",)
	verbose_name = "Nozzle"
	verbose_name_plural = "Nozzles"


class TankInline(nested_admin.NestedStackedInline):
	model = Tank
	form = TankInlineForm
	inlines = (NozzleInline,)
	classes = ("fuel-tanks-inline",)
	extra = 0
	fields = ("name", "product", "capacity_litres")
	verbose_name = " "
	verbose_name_plural = "Tanks"


class EFTDrivewayInline(nested_admin.NestedTabularInline):
	model = SpeedPoint
	form = SpeedPointInlineForm
	extra = 0
	classes = ("fuel-eft-driveway-inline",)
	fields = ("point_id", "serial_number")
	verbose_name = "EFT/Driveway"
	verbose_name_plural = "EFT/Driveway"


class ManualShopPointInline(nested_admin.NestedTabularInline):
	model = ManualShopPoint
	form = SpeedPointInlineForm
	extra = 0
	classes = ("fuel-manual-shop-inline",)
	fields = ("point_id", "serial_number")
	verbose_name = "Manual/Shop"
	verbose_name_plural = "Manual/Shop"


class CashUpAdditionalInline(nested_admin.NestedTabularInline):
	model = CashUpAdditional
	extra = 0
	classes = ("fuel-cash-up-additional-inline",)
	fields = ("additional_field",)
	verbose_name = "Cash up additional"
	verbose_name_plural = "Cash up additional"


class SiteAdminForm(forms.ModelForm):
	class Meta:
		model = Site
		fields = "__all__"
		widgets = {
			"name": forms.TextInput(attrs={"style": "width: 50%; max-width: 260px; min-width: 120px;"}),
			"currency": forms.TextInput(attrs={"style": "width: 6ch; max-width: 100%;"}),
			"manager_name": forms.TextInput(attrs={"style": "width: 50%; max-width: 260px; min-width: 120px;"}),
			"contact_number": forms.TextInput(attrs={"style": "width: 50%; max-width: 260px; min-width: 120px;"}),
			"contact_email": forms.EmailInput(attrs={"style": "width: 50%; max-width: 260px; min-width: 120px;"}),
		}


class SiteAdmin(nested_admin.NestedModelAdmin):
	form = SiteAdminForm
	change_form_template = "admin/ledger/site/change_form.html"
	inlines = (TankInline, EFTDrivewayInline, ManualShopPointInline, CashUpAdditionalInline)
	list_display = ("name", "currency", "manager_name", "contact_number", "contact_email", "is_active")
	search_fields = ("name", "currency", "address", "contact_email", "contact_number", "manager_name")
	list_filter = ("is_active",)
	fieldsets = (
		("Site details", {"fields": ("name", "currency", "is_active")}),
		("Contact Details", {"fields": ("manager_name", "contact_number", "contact_email")}),
		("Shifts", {"fields": ("shift_count",)}),
		("Address", {"fields": ("address",)}),
	)

	def save_model(self, request, obj, form, change):
		before = {}
		if change:
			original = Site.objects.get(pk=obj.pk)
			tracked_fields = ("name", "currency", "address", "contact_number", "contact_email", "manager_name", "shift_count", "is_active")
			before = {name: str(getattr(original, name)) for name in tracked_fields}
		super().save_model(request, obj, form, change)
		after = {name: str(getattr(obj, name)) for name in ("name", "currency", "address", "contact_number", "contact_email", "manager_name", "shift_count", "is_active")}
		if not change or form.changed_data:
			_record_admin_audit(obj, request.user, "Site", obj.name, "changed" if change else "created", before, after)

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		for formset in formsets:
			_record_site_inline_changes(request, form.instance, formset)

	class Media:
		css = {"all": ("css/site_shift_selector.css",)}
		js = ("js/site_shift_selector.js",)


admin.site.unregister(User)
admin.site.register(User, FuelLedgerUserAdmin)
admin.site.register(Site, SiteAdmin)
admin.site.register(FuelBooking)
admin.site.register(DailyFuelReading)
admin.site.register(TankDipReading)
admin.site.register(NozzleFuelReading)
@admin.register(SiteAuditEvent)
class SiteAuditEventAdmin(admin.ModelAdmin):
	list_display = ("site", "origin", "actor", "category", "object_label", "action", "created_at")
	list_filter = ("origin", "category", "action")
	search_fields = ("site__name", "actor__username", "category", "object_label")
	readonly_fields = ("site", "origin", "actor", "category", "object_label", "action", "before", "after", "created_at")
admin.site.register(SiteGroup)
admin.site.site_header = "FuelLedger administration"
admin.site.site_title = "FuelLedger"
