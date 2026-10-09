from django import forms
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, User
from django.shortcuts import redirect
from django.urls import reverse
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


def _can_manage_admin_site(request):
	if not request.current_site:
		return False
	if request.user.is_superuser:
		return True
	try:
		profile = request.user.fuel_profile
	except UserProfile.DoesNotExist:
		return False
	return profile.role == UserProfile.Role.HQ or (
		profile.role == UserProfile.Role.SITE
		and profile.site_id == request.current_site.pk
		and profile.can_manage_users
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
	fields = ("role", "site", "phone_number", "form_permissions", "can_configure", "can_manage_users")
	verbose_name = "FuelLedger access"
	verbose_name_plural = "FuelLedger permissions"

	def has_view_permission(self, request, obj=None):
		return _can_manage_admin_site(request) or super().has_view_permission(request, obj)

	def has_add_permission(self, request, obj=None):
		return _can_manage_admin_site(request) or super().has_add_permission(request, obj)

	def has_change_permission(self, request, obj=None):
		return _can_manage_admin_site(request) or super().has_change_permission(request, obj)

	def has_delete_permission(self, request, obj=None):
		return _can_manage_admin_site(request) or super().has_delete_permission(request, obj)

	def get_extra(self, request, obj=None, **kwargs):
		return 0 if obj else 1

	def get_formset(self, request, obj=None, **kwargs):
		formset = super().get_formset(request, obj, **kwargs)
		if request.current_site:
			formset.form.base_fields["site"].queryset = Site.objects.filter(pk=request.current_site.pk)
			formset.form.base_fields["site"].initial = request.current_site.pk
			formset.form.base_fields["role"].choices = [(UserProfile.Role.SITE, UserProfile.Role.SITE.label)]
			formset.form.base_fields["role"].initial = UserProfile.Role.SITE
		return formset


class FuelLedgerUserChangeForm(forms.ModelForm):
	phone_number = forms.CharField(max_length=30, required=False, label="Phone number")

	class Meta:
		model = User
		fields = ("username", "first_name", "last_name", "email", "phone_number", "is_active", "groups", "user_permissions")

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		if self.instance and self.instance.pk:
			try:
				self.fields["phone_number"].initial = self.instance.fuel_profile.phone_number
			except UserProfile.DoesNotExist:
				pass

	def save(self, commit=True):
		user = super().save(commit=False)
		if commit:
			user.save()
			profile, _ = UserProfile.objects.get_or_create(user=user)
			profile.phone_number = self.cleaned_data.get("phone_number", "")
			profile.save(update_fields=["phone_number"])
		return user


class FuelLedgerUserCreationForm(UserCreationForm):
	phone_number = forms.CharField(max_length=30, required=False, label="Phone number")

	class Meta:
		model = User
		fields = ("username", "first_name", "last_name", "email", "phone_number")

	def save(self, commit=True):
		user = super().save(commit=False)
		if commit:
			user.save()
			profile, _ = UserProfile.objects.get_or_create(user=user)
			profile.phone_number = self.cleaned_data.get("phone_number", "")
			profile.save(update_fields=["phone_number"])
		return user


class FuelLedgerUserAdmin(UserAdmin):
	form = FuelLedgerUserChangeForm
	add_form = FuelLedgerUserCreationForm
	inlines = ()
	add_fieldsets = (
		(None, {"classes": ("wide",), "fields": ("username", "first_name", "last_name", "email", "phone_number", "password1", "password2")}),
		("Permissions", {"fields": ("is_active", "groups", "user_permissions")}),
		("Important dates", {"fields": ("last_login", "date_joined")}),
	)

	def _site_scope_allowed(self, request):
		if not request.current_site:
			return False
		if request.user.is_superuser:
			return True
		try:
			profile = request.user.fuel_profile
		except UserProfile.DoesNotExist:
			return False
		return profile.role == UserProfile.Role.HQ or (
			profile.role == UserProfile.Role.SITE
			and profile.site_id == request.current_site.pk
			and profile.can_manage_users
		)

	def _hq_admin_allowed(self, request):
		if request.user.is_superuser:
			return True
		try:
			return request.user.fuel_profile.role == UserProfile.Role.HQ
		except UserProfile.DoesNotExist:
			return False

	def has_module_permission(self, request):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_module_permission(request)

	def has_view_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_view_permission(request, obj)

	def has_add_permission(self, request):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_add_permission(request)

	def has_change_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_change_permission(request, obj)

	def has_delete_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_delete_permission(request, obj)

	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		if request.current_site:
			queryset = queryset.filter(fuel_profile__site=request.current_site)
		return queryset

	def get_fieldsets(self, request, obj=None):
		if request.current_site:
			if obj is None:
				return ((None, {"classes": ("wide",), "fields": ("username", "password1", "password2", "phone_number")}),)
			return (
				(None, {"fields": ("username", "password")}),
				("Personal info", {"fields": ("first_name", "last_name", "email", "phone_number")}),
				("Permissions", {"fields": ("is_active", "groups", "user_permissions")}),
				("Important dates", {"fields": ("last_login", "date_joined")}),
			)
		fieldsets = list(super().get_fieldsets(request, obj))
		for index, (name, options) in enumerate(fieldsets):
			if name == "Personal info":
				fields = list(options["fields"])
				if "phone_number" not in fields:
					fields.insert(len(fields), "phone_number")
				options["fields"] = tuple(fields)
				break
		return tuple(fieldsets)

	def formfield_for_manytomany(self, db_field, request, **kwargs):
		if request.current_site and db_field.name == "groups":
			kwargs["queryset"] = Group.objects.filter(fuel_site_group__site=request.current_site)
		return super().formfield_for_manytomany(db_field, request, **kwargs)

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		if request.current_site:
			profile, _ = UserProfile.objects.get_or_create(
				user=form.instance,
				defaults={
					"role": UserProfile.Role.SITE,
					"site": request.current_site,
					"form_permissions": [key for key, _ in SITE_FORM_PERMISSION_CHOICES],
				},
			)
		else:
			try:
				profile = form.instance.fuel_profile
			except UserProfile.DoesNotExist:
				return
		if profile.site_id:
			for site_group in SiteGroup.objects.filter(site=profile.site).prefetch_related("users"):
				site_group.users.set(site_group.django_group.user_set.filter(fuel_profile__site=profile.site))
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

	def response_add(self, request, obj, post_url_continue=None):
		if request.current_site:
			return redirect(reverse("site_admin:auth_user_change", args=[obj.pk]))
		return super().response_add(request, obj, post_url_continue)


class SiteGroupInlineForm(forms.ModelForm):
	form_permissions = forms.MultipleChoiceField(
		choices=SITE_FORM_PERMISSION_CHOICES,
		label="Site form permissions",
		required=False,
		widget=FilteredSelectMultiple("site form permissions", False),
	)

	class Meta:
		model = SiteGroup
		fields = ("site", "name", "form_permissions")


class SiteGroupInline(admin.StackedInline):
	model = SiteGroup
	form = SiteGroupInlineForm
	fk_name = "django_group"
	extra = 1
	max_num = 1
	fields = ("site", "name", "form_permissions")
	verbose_name = "FuelLedger site access"
	verbose_name_plural = "FuelLedger site access"

	def has_view_permission(self, request, obj=None):
		return _can_manage_admin_site(request) or super().has_view_permission(request, obj)

	def has_add_permission(self, request, obj=None):
		return _can_manage_admin_site(request) or super().has_add_permission(request, obj)

	def has_change_permission(self, request, obj=None):
		return _can_manage_admin_site(request) or super().has_change_permission(request, obj)

	def has_delete_permission(self, request, obj=None):
		return _can_manage_admin_site(request) or super().has_delete_permission(request, obj)

	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		if request.current_site:
			queryset = queryset.filter(site=request.current_site)
		return queryset

	def formfield_for_foreignkey(self, db_field, request, **kwargs):
		if request.current_site and db_field.name == "site":
			kwargs["queryset"] = Site.objects.filter(pk=request.current_site.pk)
			kwargs["initial"] = request.current_site.pk
		return super().formfield_for_foreignkey(db_field, request, **kwargs)


class SiteScopedUserAdmin(UserAdmin):
	form = FuelLedgerUserChangeForm
	add_form = FuelLedgerUserCreationForm
	add_fieldsets = (
		(None, {"classes": ("wide",), "fields": ("username", "first_name", "last_name", "email", "phone_number", "password1", "password2", "is_active")}),
		("Permissions", {"fields": ("groups", "user_permissions")}),
		("Important dates", {"fields": ("last_login", "date_joined")}),
	)

	def _site_scope_allowed(self, request):
		if not request.current_site:
			return False
		if request.user.is_superuser:
			return True
		try:
			profile = request.user.fuel_profile
		except UserProfile.DoesNotExist:
			return False
		return profile.role == UserProfile.Role.HQ or (
			profile.role == UserProfile.Role.SITE
			and profile.site_id == request.current_site.pk
			and profile.can_manage_users
		)

	def has_module_permission(self, request):
		return self._site_scope_allowed(request) or super().has_module_permission(request)

	def has_view_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or super().has_view_permission(request, obj)

	def has_add_permission(self, request):
		return self._site_scope_allowed(request) or super().has_add_permission(request)

	def has_change_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or super().has_change_permission(request, obj)

	def has_delete_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or super().has_delete_permission(request, obj)

	def save_model(self, request, obj, form, change):
		super().save_model(request, obj, form, change)
		if not request.current_site:
			return
		profile, _ = UserProfile.objects.get_or_create(user=obj)
		profile.role = UserProfile.Role.SITE
		profile.site = request.current_site
		profile.phone_number = form.cleaned_data.get("phone_number", "")
		profile.form_permissions = list(profile.form_permissions or [])
		profile.save(update_fields=["role", "site", "phone_number", "form_permissions"])

	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		if request.current_site:
			queryset = queryset.filter(fuel_profile__site=request.current_site)
		return queryset

	def get_fieldsets(self, request, obj=None):
		if request.current_site:
			if obj is None:
				return self.add_fieldsets
			return (
				(None, {"fields": ("username", "password")}),
				("Personal info", {"fields": ("first_name", "last_name", "email", "phone_number")}),
				("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
				("Important dates", {"fields": ("last_login", "date_joined")}),
			)
		return super().get_fieldsets(request, obj)

	def formfield_for_manytomany(self, db_field, request, **kwargs):
		if request.current_site and db_field.name == "groups":
			kwargs["queryset"] = Group.objects.filter(fuel_site_group__site=request.current_site)
		return super().formfield_for_manytomany(db_field, request, **kwargs)


class SiteScopedGroupAdmin(GroupAdmin):
	inlines = (SiteGroupInline,)

	def _site_scope_allowed(self, request):
		if not request.current_site:
			return False
		if request.user.is_superuser:
			return True
		try:
			profile = request.user.fuel_profile
		except UserProfile.DoesNotExist:
			return False
		return profile.role == UserProfile.Role.HQ or (
			profile.role == UserProfile.Role.SITE
			and profile.site_id == request.current_site.pk
			and profile.can_manage_users
		)

	def has_module_permission(self, request):
		return self._site_scope_allowed(request) or super().has_module_permission(request)

	def has_view_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or super().has_view_permission(request, obj)

	def has_add_permission(self, request):
		return self._site_scope_allowed(request) or super().has_add_permission(request)

	def has_change_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or super().has_change_permission(request, obj)

	def has_delete_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or super().has_delete_permission(request, obj)

	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		if request.current_site:
			queryset = queryset.filter(fuel_site_group__site=request.current_site)
		return queryset


class FuelLedgerGroupAdmin(GroupAdmin):
	inlines = (SiteGroupInline,)

	def _site_scope_allowed(self, request):
		if not request.current_site:
			return False
		if request.user.is_superuser:
			return True
		try:
			profile = request.user.fuel_profile
		except UserProfile.DoesNotExist:
			return False
		return profile.role == UserProfile.Role.HQ or (
			profile.role == UserProfile.Role.SITE
			and profile.site_id == request.current_site.pk
			and profile.can_manage_users
		)

	def _hq_admin_allowed(self, request):
		if request.user.is_superuser:
			return True
		try:
			return request.user.fuel_profile.role == UserProfile.Role.HQ
		except UserProfile.DoesNotExist:
			return False

	def has_module_permission(self, request):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_module_permission(request)

	def has_view_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_view_permission(request, obj)

	def has_add_permission(self, request):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_add_permission(request)

	def has_change_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_change_permission(request, obj)

	def has_delete_permission(self, request, obj=None):
		return self._site_scope_allowed(request) or self._hq_admin_allowed(request) or super().has_delete_permission(request, obj)

	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		if request.current_site:
			queryset = queryset.filter(fuel_site_group__site=request.current_site)
		return queryset


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
	verbose_name = "EFT"
	verbose_name_plural = "EFT"


class ManualShopPointInline(nested_admin.NestedTabularInline):
	model = ManualShopPoint
	form = SpeedPointInlineForm
	extra = 0
	classes = ("fuel-manual-shop-inline",)
	fields = ("point_id", "serial_number")
	verbose_name = "Manual"
	verbose_name_plural = "Manual"


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
		("Site details", {"fields": ("name", "currency", "image", "is_active")}),
		("Contact Details", {"fields": ("manager_name", "contact_number", "contact_email")}),
		("Shifts", {"fields": ("shift_count",)}),
		("Readings used by site", {"fields": ("use_tank_readings", "use_fuel_sales", "use_mechanical_readings", "use_electronic_readings")}),
		("Address", {"fields": ("address",)}),
	)

	def _is_hq_user(self, request):
		if request.user.is_superuser:
			return True
		try:
			return request.user.fuel_profile.role == UserProfile.Role.HQ
		except UserProfile.DoesNotExist:
			return False

	def has_module_permission(self, request):
		return self._is_hq_user(request) or super().has_module_permission(request)

	def has_view_permission(self, request, obj=None):
		return self._is_hq_user(request) or super().has_view_permission(request, obj)

	def has_add_permission(self, request):
		return self._is_hq_user(request) or super().has_add_permission(request)

	def has_change_permission(self, request, obj=None):
		return self._is_hq_user(request) or super().has_change_permission(request, obj)

	def has_delete_permission(self, request, obj=None):
		return self._is_hq_user(request) or super().has_delete_permission(request, obj)

	def save_model(self, request, obj, form, change):
		before = {}
		if change:
			original = Site.objects.get(pk=obj.pk)
			tracked_fields = ("name", "currency", "image", "address", "contact_number", "contact_email", "manager_name", "shift_count", "use_tank_readings", "use_fuel_sales", "use_mechanical_readings", "use_electronic_readings", "is_active")
			before = {name: str(getattr(original, name)) for name in tracked_fields}
		super().save_model(request, obj, form, change)
		after = {name: str(getattr(obj, name)) for name in ("name", "currency", "image", "address", "contact_number", "contact_email", "manager_name", "shift_count", "use_tank_readings", "use_fuel_sales", "use_mechanical_readings", "use_electronic_readings", "is_active")}
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
admin.site.unregister(Group)
admin.site.register(Group, FuelLedgerGroupAdmin)
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


def _hq_admin_permission(request):
	if not request.user.is_active:
		return False
	if request.user.is_staff:
		return True
	try:
		return request.user.fuel_profile.role == UserProfile.Role.HQ
	except UserProfile.DoesNotExist:
		return False


admin.site.has_permission = _hq_admin_permission


class SiteScopedAdminSite(AdminSite):
	def has_permission(self, request):
		if not request.user.is_active or not request.current_site:
			return False
		if request.user.is_superuser:
			return True
		try:
			profile = request.user.fuel_profile
		except UserProfile.DoesNotExist:
			return False
		return profile.role == UserProfile.Role.HQ or (
			profile.role == UserProfile.Role.SITE
			and profile.site_id == request.current_site.pk
			and profile.can_manage_users
		)


site_admin_site = SiteScopedAdminSite(name="site_admin")
site_admin_site.site_header = "FuelLedger site access"
site_admin_site.site_title = "FuelLedger site access"
site_admin_site.register(User, SiteScopedUserAdmin)
site_admin_site.register(Group, SiteScopedGroupAdmin)


def _site_admin_home(request):
	return redirect("site-dashboard", slug=request.current_site.slug)


site_admin_site.index = _site_admin_home
