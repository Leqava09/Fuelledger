from django import forms
from django.contrib.auth.models import User
from django.utils import timezone

from .models import SiteGroup


class BookInFuelForm(forms.Form):
	business_date = forms.DateField(widget=forms.HiddenInput)

	def __init__(self, tanks, *args, initial_quantities=None, **kwargs):
		super().__init__(*args, **kwargs)
		self.tanks = list(tanks)
		self.tank_fields = []
		initial_quantities = initial_quantities or {}
		for tank in self.tanks:
			field_name = f"tank_{tank.pk}_qty_litres"
			self.fields[field_name] = forms.DecimalField(
				label="Qty L booked in",
				required=False,
				min_value=0,
				max_digits=12,
				decimal_places=2,
				initial=initial_quantities.get(tank.pk),
				widget=forms.NumberInput(attrs={"class": "quantity-input", "min": "0", "step": "0.01"}),
			)
			self.tank_fields.append((tank, field_name))

	def clean_business_date(self):
		business_date = self.cleaned_data["business_date"]
		if business_date > timezone.localdate():
			raise forms.ValidationError("Book-in dates cannot be in the future.")
		return business_date


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


class DailyFuelReadingForm(forms.Form):
	business_date = forms.DateField(widget=forms.HiddenInput)
	shift_label = forms.CharField(widget=forms.HiddenInput)

	def __init__(self, site, *args, initial_readings=None, **kwargs):
		super().__init__(*args, **kwargs)
		self.site = site
		initial_readings = initial_readings or {"dips": {}, "nozzles": {}}
		self.tank_fields = []
		self.nozzle_fields = []
		for tank in site.tanks.order_by("name"):
			field_name = f"tank_{tank.pk}_dip"
			self.fields[field_name] = forms.DecimalField(
				label="Dip reading (L)",
				min_value=0,
				max_digits=12,
				decimal_places=2,
				initial=initial_readings["dips"].get(tank.pk),
				widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
			)
			self.tank_fields.append((tank, field_name))
			for nozzle in tank.nozzles.order_by("name"):
				mechanical_name = f"nozzle_{nozzle.pk}_mechanical"
				electronic_name = f"nozzle_{nozzle.pk}_electronic"
				previous = initial_readings["nozzles"].get(nozzle.pk, {})
				self.fields[mechanical_name] = forms.DecimalField(
					label="Mechanical reading",
					min_value=0,
					max_digits=14,
					decimal_places=2,
					initial=previous.get("mechanical"),
					widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
				)
				self.fields[electronic_name] = forms.DecimalField(
					label="Electronic reading",
					min_value=0,
					max_digits=14,
					decimal_places=2,
					initial=previous.get("electronic"),
					widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
				)
				self.nozzle_fields.append((tank, nozzle, mechanical_name, electronic_name))

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


class SiteUserCreateForm(forms.Form):
	username = forms.CharField(max_length=150)
	first_name = forms.CharField(max_length=150, required=False)
	last_name = forms.CharField(max_length=150, required=False)
	email = forms.EmailField(required=False)
	password = forms.CharField(widget=forms.PasswordInput)
	confirm_password = forms.CharField(label="Confirm password", widget=forms.PasswordInput)
	groups = forms.ModelMultipleChoiceField(queryset=SiteGroup.objects.none(), required=False)

	def __init__(self, site, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.site = site
		self.fields["groups"].queryset = SiteGroup.objects.filter(site=site).order_by("name")

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
	users = forms.ModelMultipleChoiceField(queryset=User.objects.none(), required=False)

	class Meta:
		model = SiteGroup
		fields = ("name", "users")

	def __init__(self, site, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.site = site
		self.fields["users"].queryset = User.objects.filter(fuel_profile__site=site).order_by("username")

	def clean_name(self):
		name = self.cleaned_data["name"].strip()
		if SiteGroup.objects.filter(site=self.site, name__iexact=name).exclude(pk=self.instance.pk).exists():
			raise forms.ValidationError("A group with this name already exists at this site.")
		return name