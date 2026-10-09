from io import BytesIO
import calendar
import json
import re
import tempfile
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from html import unescape
from datetime import date, timedelta

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from ledger.calculations import FormulaError, calculate_values, validate_formula
from ledger.admin import CashUpAdditionalInline, EFTDrivewayInline, ManualShopPointInline, NozzleInlineForm, SiteAdmin, SiteAdminForm, SpeedPointInlineForm, TankInlineForm
from ledger.models import (
    CashUpAdditional,
    CashUpField,
    CashUpShiftRecord,
    DailyFuelReading,
    FuelBooking,
    FuelTransfer,
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
)


class CalculationTests(TestCase):
    def test_calculated_cash_difference_treats_blank_optional_values_as_zero(self):
        fields = [
            CashUpField(key="cash", field_type=CashUpField.FieldType.MONEY),
            CashUpField(key="pos", field_type=CashUpField.FieldType.MONEY),
            CashUpField(key="difference", field_type=CashUpField.FieldType.CALCULATED, formula="cash - pos"),
        ]

        values = calculate_values(fields, {"cash": "100.00", "pos": "78.25"})

        self.assertEqual(values["difference"], "21.75")

    def test_formula_rejects_python_calls(self):
        with self.assertRaises(FormulaError):
            validate_formula("__import__('os')", ["cash"])


class SiteConfigurationAdminTests(TestCase):
    def test_admin_home_redirects_to_hq_home(self):
        admin_user = User.objects.create_superuser(username="home-admin", password="test-pass-123", email="home-admin@example.com")
        client = Client()
        client.force_login(admin_user)

        response = client.get(reverse("admin:index"))

        self.assertRedirects(response, reverse("hq-dashboard"), fetch_redirect_response=False)

    def test_grappelli_site_form_contains_contact_fields_and_nested_tank_nozzle_inlines(self):
        admin_user = User.objects.create_superuser(username="admin", password="test-pass-123", email="admin@example.com")
        client = Client()
        client.force_login(admin_user)

        response = client.get(reverse("admin:ledger_site_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Address")
        self.assertContains(response, "Contact email")
        self.assertContains(response, "Contact Number")
        self.assertContains(response, "Currency")
        self.assertContains(response, "Site picture")
        self.assertContains(response, 'name="image"')
        self.assertContains(response, "Manager name")
        self.assertContains(response, "Site Name")
        self.assertContains(response, "Amount of shifts")
        self.assertContains(response, "Readings used by site")
        for field_name in ("use_tank_readings", "use_fuel_sales", "use_mechanical_readings", "use_electronic_readings"):
            self.assertContains(response, f'name="{field_name}"')
        self.assertContains(response, "Tanks")
        self.assertContains(response, "Tank capacity (L)")
        self.assertContains(response, "Nozzles")
        self.assertContains(response, "EFT/Driveway")
        self.assertContains(response, "EFT/Driveway no/ID")
        self.assertContains(response, "EFT/Driveway Serial No")
        self.assertContains(response, "Manual/Shop")
        self.assertContains(response, "Manual/Shop no/ID")
        self.assertContains(response, "Manual/Shop Serial No")
        self.assertContains(response, "Cash up additional")
        self.assertContains(response, "nested_admin")
        self.assertContains(response, "site_shift_selector.js")
        self.assertContains(response, "fuel-tanks-inline")
        self.assertContains(response, "fuel-eft-driveway-inline")
        self.assertContains(response, "fuel-manual-shop-inline")
        self.assertContains(response, "fuel-cash-up-additional-inline")
        html = response.content.decode()
        self.assertNotIn('id="id_slug"', html)
        field_positions = [html.index(f'id="id_{field}"') for field in ("manager_name", "contact_number", "contact_email", "address")]
        self.assertEqual(field_positions, sorted(field_positions))
        for field_name in ("name", "manager_name", "contact_number", "contact_email"):
            self.assertIn("width: 50%", html[html.index(f'id="id_{field_name}"') - 300:html.index(f'id="id_{field_name}"') + 300])

    def test_hq_user_admin_uses_standard_user_permissions_without_fuelledger_inline(self):
        admin_user = User.objects.create_superuser(username="permission-admin", password="test-pass-123", email="permission-admin@example.com")
        site = Site.objects.create(name="Permission Admin Site")
        site_user = User.objects.create_user(username="permission-site-user", password="test-pass-123")
        UserProfile.objects.create(user=site_user, role=UserProfile.Role.SITE, site=site)
        client = Client()
        client.force_login(admin_user)

        response = client.get(reverse("admin:auth_user_change", args=[site_user.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User permissions")
        self.assertContains(response, "selectfilter")
        self.assertNotContains(response, "FuelLedger permissions")
        self.assertNotContains(response, "FuelLedger access")

    def test_site_user_form_exposes_standard_user_permissions(self):
        site = Site.objects.create(name="Site Permissions Test")
        admin_user = User.objects.create_user(username="site-permission-admin", password="test-pass-123")
        UserProfile.objects.create(user=admin_user, role=UserProfile.Role.HQ)
        client = Client()
        client.force_login(admin_user)

        response = client.get(reverse("site-user-create", args=[site.slug]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("site_admin:auth_user_add"))

    def test_hq_and_site_users_include_phone_number_field(self):
        admin_user = User.objects.create_superuser(username="phone-admin", password="test-pass-123", email="phone-admin@example.com")
        site = Site.objects.create(name="Phone Field Site")
        site_user = User.objects.create_user(username="phone-site-user", password="test-pass-123")
        UserProfile.objects.create(user=site_user, role=UserProfile.Role.SITE, site=site)
        client = Client()
        client.force_login(admin_user)

        admin_response = client.get(reverse("admin:auth_user_change", args=[site_user.pk]))
        self.assertEqual(admin_response.status_code, 200)
        self.assertContains(admin_response, "Phone number")

        site_user_admin = User.objects.create_user(username="site-phone-admin", password="test-pass-123")
        UserProfile.objects.create(user=site_user_admin, role=UserProfile.Role.HQ)
        client.force_login(site_user_admin)
        site_response = client.get(reverse("site-user-create", args=[site.slug]))
        self.assertEqual(site_response.status_code, 302)
        self.assertEqual(site_response["Location"], reverse("site_admin:auth_user_add"))

    def test_site_registration_contains_currency_and_cash_up_additional_inline(self):
        admin_user = User.objects.create_superuser(username="currency-admin", password="test-pass-123", email="currency-admin@example.com")
        client = Client()
        client.force_login(admin_user)

        response = client.get(reverse("admin:ledger_site_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Currency")
        self.assertContains(response, "Cash up additional")
        self.assertEqual(SiteAdmin.fieldsets[-1][0], "Address")
        self.assertEqual(SiteAdminForm().fields["currency"].max_length, 5)
        self.assertEqual(SiteAdminForm().fields["currency"].required, False)
        self.assertEqual(SiteAdminForm().fields["currency"].widget.attrs["style"], "width: 6ch; max-width: 100%;")
        self.assertIsNone(CashUpAdditionalInline.max_num)
        self.assertEqual(CashUpAdditionalInline.extra, 0)
        self.assertEqual(CashUpAdditionalInline.fields, ("additional_field",))
        self.assertEqual(CashUpAdditional._meta.get_field("additional_field").max_length, 30)
        self.assertIn("currency", SiteAdmin.list_display)
        html = response.content.decode()
        self.assertLess(html.index("Cash up additional"), html.index('id="id_address"'))
        self.assertLess(html.index("Manual/Shop"), html.index("Cash up additional"))
        self.assertIn("Add another Cash up additional", html)
        site = Site.objects.create(name="Additional Fields Site")
        CashUpAdditional.objects.create(site=site, additional_field="Till variance")
        CashUpAdditional.objects.create(site=site, additional_field="Manager note")
        self.assertEqual(site.cash_up_additional_fields.count(), 2)
        inline_formset = next(
            inline.formset
            for inline in response.context["inline_admin_formsets"]
            if inline.opts.model is CashUpAdditional
        )
        self.assertEqual(len(inline_formset.forms), 0)

    def test_speed_point_is_attached_to_its_site(self):
        site = Site.objects.create(name="Speed Point Test")
        point = SpeedPoint.objects.create(site=site, point_id="SP-1", serial_number="SER-001")

        self.assertEqual(site.speed_points.get(), point)
        self.assertEqual(str(point), "SP-1")

    def test_speed_point_fields_are_side_by_side_and_limited(self):
        form = SpeedPointInlineForm()

        self.assertEqual(form.fields["point_id"].max_length, 10)
        self.assertEqual(form.fields["point_id"].widget.attrs["size"], 10)
        self.assertEqual(form.fields["serial_number"].max_length, 30)
        self.assertEqual(form.fields["serial_number"].widget.attrs["size"], 30)
        self.assertEqual(EFTDrivewayInline.extra, 0)
        self.assertEqual(EFTDrivewayInline.fields, ("point_id", "serial_number"))
        self.assertEqual(ManualShopPointInline.extra, 0)
        self.assertEqual(ManualShopPointInline.fields, ("point_id", "serial_number"))

    def test_manual_shop_points_use_the_same_site_scoped_fields(self):
        site = Site.objects.create(name="Manual Shop Test")
        point = ManualShopPoint.objects.create(site=site, point_id="MS-1", serial_number="MAN-001")

        self.assertEqual(site.manual_shop_points.get(), point)
        self.assertEqual(str(point), "MS-1")

    def test_site_shift_names_match_selected_shift_count(self):
        site = Site.objects.create(name="Shift Test Site")
        for shift_count, expected in (
            (1, ["Day"]),
            (2, ["Day", "Night"]),
            (3, ["Morning", "Afternoon", "Evening"]),
        ):
            site.shift_count = shift_count
            self.assertEqual(list(site.get_shift_names()), expected)

    def test_tank_and_nozzle_inline_widgets_are_compact_and_labeled(self):
        tank_form = TankInlineForm()
        nozzle_form = NozzleInlineForm()

        self.assertEqual(tank_form.fields["name"].widget.attrs["style"], "width: 145px; max-width: 100%;")
        self.assertEqual(tank_form.fields["product"].widget.attrs["style"], "width: 170px; max-width: 100%;")
        self.assertEqual(nozzle_form.fields["name"].label, "No")
        self.assertEqual(nozzle_form.fields["name"].widget.attrs["style"], "width: 45px; max-width: 100%;")
        self.assertEqual(tank_form.fields["name"].max_length, 70)
        self.assertEqual(tank_form.fields["product"].max_length, 90)
        self.assertEqual(tank_form.fields["capacity_litres"].max_digits, 12)
        self.assertEqual(tank_form.fields["capacity_litres"].widget.attrs["step"], "0.01")
        self.assertTrue(all(
            SiteAdminForm().fields[field_name].widget.attrs["style"].startswith("width: 50%;")
            for field_name in ("name", "manager_name", "contact_number", "contact_email")
        ))

    def test_tank_heading_does_not_repeat_tank_prefix(self):
        admin_user = User.objects.create_superuser(username="tank-admin", password="test-pass-123", email="tank-admin@example.com")
        site = Site.objects.create(name="Tank Heading Test")
        Tank.objects.create(site=site, name="Tank 1", product="Diesel 50 ppm", capacity_litres="25000.00")
        client = Client()
        client.force_login(admin_user)

        response = client.get(reverse("admin:ledger_site_change", args=[site.pk]))

        self.assertEqual(response.status_code, 200)
        page_text = " ".join(re.sub(r"<[^>]*>", " ", unescape(response.content.decode())).split())
        tank_heading_fragments = re.findall(r".{0,30}Diesel 50 ppm.{0,10}", page_text)
        self.assertTrue("Tank 1 - Diesel 50 ppm" in page_text, repr(tank_heading_fragments))
        self.assertIn('id="id_tanks-0-product"', response.content.decode())
        self.assertIn('id="id_tanks-0-capacity_litres"', response.content.decode())
        self.assertContains(response, "25000.00")

    def test_site_admin_audits_inline_tank_changes(self):
        admin_user = User.objects.create_superuser(username="audit-admin", password="test-pass-123", email="audit-admin@example.com")
        site = Site.objects.create(name="Audit Test Site")
        tank = Tank.objects.create(site=site, name="Tank 1", product="Diesel")
        formset = SimpleNamespace(model=Tank, new_objects=[tank], changed_objects=[], deleted_objects=[], forms=[])
        site_admin = SiteAdmin(Site, admin.site)

        with patch("nested_admin.NestedModelAdmin.save_related"):
            site_admin.save_related(
                SimpleNamespace(user=admin_user),
                SimpleNamespace(instance=site),
                [formset],
                change=True,
            )

        event = SiteAuditEvent.objects.get(site=site, category="Tank")
        self.assertEqual(event.actor, admin_user)
        self.assertEqual(event.action, "created")
        self.assertEqual(event.object_label, str(tank))


class SiteAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.site_one = Site.objects.create(name="Puma Central")
        self.site_two = Site.objects.create(name="Texan North")
        self.site_user = User.objects.create_user(username="cashier", password="test-pass-123")
        UserProfile.objects.create(user=self.site_user, role=UserProfile.Role.SITE, site=self.site_one)
        self.hq_user = User.objects.create_user(username="hq", password="test-pass-123")
        UserProfile.objects.create(user=self.hq_user, role=UserProfile.Role.HQ)

    def test_login_brand_panel_omits_goshen_label_and_uses_centered_layout(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FuelLedger")
        self.assertContains(response, "login-aside")
        self.assertNotContains(response, "Goshen Food Solutions")

    def test_site_user_is_limited_to_assigned_site_and_cannot_open_hq(self):
        self.client.force_login(self.site_user)

        self.assertEqual(self.client.get(reverse("hq-dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("site-dashboard", args=[self.site_two.slug])).status_code, 403)
        self.assertEqual(self.client.get(reverse("site-dashboard", args=[self.site_one.slug])).status_code, 200)

    def test_site_user_cannot_open_admin_even_if_marked_staff(self):
        self.site_user.is_staff = True
        self.site_user.save(update_fields=["is_staff"])
        self.client.force_login(self.site_user)

        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 403)

    def test_hq_user_can_open_hq_and_any_site(self):
        self.client.force_login(self.hq_user)

        self.site_two.currency = "ZAR"
        self.site_two.manager_name = "Site Manager"
        self.site_two.address = "1 Main Road"
        self.site_two.contact_number = "0123456789"
        self.site_two.contact_email = "site@example.com"
        self.site_two.save(update_fields=["currency", "manager_name", "address", "contact_number", "contact_email"])
        self.assertEqual(self.client.get(reverse("hq-dashboard")).status_code, 200)
        response = self.client.get(reverse("site-dashboard", args=[self.site_two.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("hq-dashboard"))
        self.assertContains(response, "SITE")
        self.assertNotContains(response, f"SITE NUMBER {self.site_two.pk}")
        self.assertNotContains(response, "ZAR")
        self.assertContains(response, "Site Manager")
        self.assertContains(response, "1 Main Road")
        self.assertContains(response, "0123456789")
        self.assertContains(response, "site@example.com")
        hq_response = self.client.get(reverse("hq-dashboard"))
        self.assertNotContains(hq_response, "ZAR")
        self.assertContains(hq_response, reverse("admin:auth_user_changelist"))
        self.assertContains(hq_response, reverse("admin:auth_group_changelist"))
        self.assertNotContains(hq_response, reverse("site-users", args=[self.site_two.slug]))
        self.assertContains(hq_response, reverse("admin:ledger_site_changelist"))
        self.assertContains(hq_response, "hq-site-card")
        self.assertContains(hq_response, self.site_two.name)
        self.assertEqual(hq_response.content.decode().count(f'class="hq-site-picture-title">{self.site_two.name}</span>'), 1)
        self.assertNotContains(hq_response, f"<h3>{self.site_two.name}</h3>")
        self.assertNotContains(hq_response, f"SITE {self.site_two.pk}")
        self.assertNotContains(hq_response, "Users and groups")
        self.assertNotContains(hq_response, "Latest submissions")
        self.assertEqual(self.client.get(reverse("site-users", args=[self.site_two.slug])).status_code, 200)
        self.assertEqual(self.client.get(reverse("site-groups", args=[self.site_two.slug])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:ledger_site_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:ledger_site_change", args=[self.site_two.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:auth_user_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:auth_group_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:ledger_site_delete", args=[self.site_two.pk])).status_code, 403)

    def test_uploaded_site_picture_is_displayed_on_its_site_dashboard(self):
        image_file = BytesIO()
        Image.new("RGB", (1, 1), color="teal").save(image_file, format="PNG")
        image_file.seek(0)
        self.client.force_login(self.hq_user)
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.site_one.image.save("site.png", SimpleUploadedFile("site.png", image_file.read(), content_type="image/png"))
            response = self.client.get(reverse("site-dashboard", args=[self.site_one.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "has-site-picture")
        self.assertContains(response, self.site_one.image.url)
        hq_response = self.client.get(reverse("hq-dashboard"))
        self.assertContains(hq_response, f'class="hq-site-picture-title">{self.site_one.name}</span>')

    def test_retired_cashup_form_builder_is_not_available(self):
        self.site_user.fuel_profile.can_configure = True
        self.site_user.fuel_profile.save(update_fields=["can_configure"])
        self.client.force_login(self.site_user)

        self.assertEqual(self.client.get(f"/sites/{self.site_one.slug}/configure/").status_code, 404)

    def test_site_dashboard_has_no_shift_entry_controls(self):
        self.site_user.fuel_profile.can_configure = True
        self.site_user.fuel_profile.save(update_fields=["can_configure"])
        self.client.force_login(self.site_user)

        response = self.client.get(reverse("site-dashboard", args=[self.site_one.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Enter shift")
        self.assertNotContains(response, "Recent shifts")
        self.assertNotContains(response, "All records")
        self.assertNotContains(response, "Shift form")
        self.assertNotContains(response, "Configure cash-up fields")
        self.assertEqual(self.client.get(f"/sites/{self.site_one.slug}/entries/new/").status_code, 404)

    def test_site_calendar_defaults_to_today_and_accepts_past_dates(self):
        self.client.force_login(self.site_user)
        today = timezone.localdate()
        selected_date = today - timedelta(days=30)
        url = reverse("site-dashboard", args=[self.site_one.slug])

        today_response = self.client.get(url)
        self.assertEqual(today_response.context["selected_date"], today)
        self.assertContains(today_response, f'value="{today.isoformat()}"')
        self.assertContains(today_response, "site_date_picker.js")
        self.assertNotContains(today_response, "View date")

        past_response = self.client.get(url, {"date": selected_date.isoformat()})
        self.assertEqual(past_response.context["selected_date"], selected_date)
        self.assertNotContains(past_response, f"/sites/{self.site_one.slug}/forms/")

        selected_shift = self.client.get(url, {"date": selected_date.isoformat(), "shift": "Day"})
        for key in ("book-in-fuel",):
            self.assertContains(
                selected_shift,
                f"/sites/{self.site_one.slug}/forms/{key}/?date={selected_date.isoformat()}&amp;shift=Day",
            )
        self.assertContains(selected_shift, f"/sites/{self.site_one.slug}/forms/cash-up/cash/?date={selected_date.isoformat()}&amp;shift=Day")
        self.assertNotContains(selected_shift, f"/sites/{self.site_one.slug}/forms/cash-up/?")
        for reading_type in ("tank-reading", "mechanical-readings", "electronic-readings"):
            self.assertContains(
                selected_shift,
                f"/sites/{self.site_one.slug}/forms/fuel-readings/{reading_type}/?date={selected_date.isoformat()}&amp;shift=Day",
            )

    def test_site_dashboard_shows_forms_only_after_configured_shift_is_selected(self):
        self.site_one.shift_count = 3
        self.site_one.save(update_fields=["shift_count"])
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        self.client.force_login(self.site_user)
        selected_date = timezone.localdate() - timedelta(days=1)
        url = reverse("site-dashboard", args=[self.site_one.slug])

        date_page = self.client.get(url, {"date": selected_date.isoformat()})
        self.assertEqual(date_page.status_code, 200)
        for shift in ("Morning", "Afternoon", "Evening"):
              self.assertContains(date_page, shift)
        self.assertNotContains(date_page, f"/sites/{self.site_one.slug}/forms/")

        shift_page = self.client.get(url, {"date": selected_date.isoformat(), "shift": "Morning"})
        self.assertEqual(shift_page.status_code, 200)
        self.assertContains(shift_page, "Morning shift")
        self.assertContains(shift_page, 'id="site-shift"')
        self.assertContains(shift_page, 'name="date"')
        self.assertContains(shift_page, 'option value="Morning" selected')
        dashboard_html = shift_page.content.decode()
        date_workspace_index = dashboard_html.index('<section class="date-workspace"')
        self.assertLess(dashboard_html.index("Users and groups"), date_workspace_index)
        self.assertLess(dashboard_html.index("History"), date_workspace_index)
        for key in ("book-in-fuel",):
            self.assertContains(
                shift_page,
                f"/sites/{self.site_one.slug}/forms/{key}/?date={selected_date.isoformat()}&amp;shift=Morning",
            )
        self.assertContains(shift_page, f"/sites/{self.site_one.slug}/forms/cash-up/cash/?date={selected_date.isoformat()}&amp;shift=Morning")
        self.assertNotContains(shift_page, f"/sites/{self.site_one.slug}/forms/cash-up/?")
        self.assertContains(shift_page, "Fuel Readings")
        for reading_type in ("tank-reading", "mechanical-readings", "electronic-readings"):
            self.assertContains(
                shift_page,
                f"/sites/{self.site_one.slug}/forms/fuel-readings/{reading_type}/?date={selected_date.isoformat()}&amp;shift=Morning",
            )
        self.assertContains(shift_page, "fuel-reading-tab button")
        self.assertNotContains(shift_page, f"/sites/{self.site_one.slug}/forms/daily-fuel-readings/?")
        self.assertLess(shift_page.content.decode().index("Fuel Readings"), shift_page.content.decode().index("Fuel Movements"))
        for form_key in ("book-in-fuel", "transfer-fuel"):
            self.assertContains(
                shift_page,
                f"/sites/{self.site_one.slug}/forms/{form_key}/?date={selected_date.isoformat()}&amp;shift=Morning",
            )

    def test_future_calendar_date_defaults_back_to_today(self):
        self.client.force_login(self.site_user)
        today = timezone.localdate()
        future_date = today + timedelta(days=1)

        response = self.client.get(reverse("site-dashboard", args=[self.site_one.slug]), {"date": future_date.isoformat()})

        self.assertEqual(response.context["selected_date"], today)

    def test_blank_form_page_preserves_date_and_is_site_restricted(self):
        self.client.force_login(self.site_user)
        selected_date = timezone.localdate() - timedelta(days=2)

        form_url = reverse("blank-site-form", args=[self.site_one.slug, "book-in-fuel"])
        response = self.client.get(form_url, {
            "date": selected_date.isoformat(),
            "shift": "Day",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Book IN Fuel")
        self.assertContains(response, selected_date.strftime("%d %b %Y"))
        self.assertRedirects(
            self.client.get(form_url, {"date": selected_date.isoformat()}),
            f"{reverse('site-dashboard', args=[self.site_one.slug])}?date={selected_date.isoformat()}",
        )
        self.assertEqual(
            self.client.get(reverse("blank-site-form", args=[self.site_two.slug, "book-in-fuel"])).status_code,
            403,
        )

    def test_book_in_fuel_uses_site_tanks_and_saves_selected_date_quantities(self):
        tank = Tank.objects.create(site=self.site_one, name="Tank 1", product="Diesel")
        other_tank = Tank.objects.create(site=self.site_two, name="Tank 2", product="ULP 95")
        selected_date = timezone.localdate() - timedelta(days=3)
        url = reverse("blank-site-form", args=[self.site_one.slug, "book-in-fuel"])
        self.client.force_login(self.site_user)

        shift_url = f"{url}?date={selected_date.isoformat()}&shift=Day"
        page = self.client.get(url, {"date": selected_date.isoformat(), "shift": "Day"})

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Tank 1")
        self.assertContains(page, "Diesel")
        self.assertContains(page, "Qty L booked in")
        self.assertContains(page, "Return")
        self.assertContains(page, "Save and Continue")
        self.assertContains(page, "Save and Exit")
        self.assertLess(page.content.decode().index("fuel-workflow-actions"), page.content.decode().index('id="book-in-form"'))
        self.assertContains(page, 'data-workflow-draft-key="book-in-fuel"')
        self.assertContains(page, "data-unsaved-return")
        self.assertContains(
            page,
            f"{reverse('site-form-history', args=[self.site_one.slug, 'book-in-fuel'])}?date={selected_date.isoformat()}&amp;shift=Day",
        )
        self.assertNotContains(page, other_tank.name)

        response = self.client.post(shift_url, {
            "business_date": selected_date.isoformat(),
            "shift_label": "Day",
            "save_mode": "continue",
            "delivery_count": "1",
            "delivery_1_truck_reference": "Truck 1",
            f"delivery_1_tank_{tank.pk}_qty_litres": "1250.50",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('blank-site-form', args=[self.site_one.slug, 'book-in-fuel'])}?date={selected_date.isoformat()}&shift=Day&workflow_saved=book-in-fuel")
        booking = FuelBooking.objects.get(site=self.site_one, tank=tank, business_date=selected_date, shift_label="Day")
        self.assertEqual(str(booking.qty_litres), "1250.50")
        self.assertEqual(booking.delivery_number, 1)
        self.assertEqual(booking.truck_reference, "Truck 1")
        reopened = self.client.get(url, {"date": selected_date.isoformat(), "shift": "Day"})
        self.assertContains(reopened, 'value="1250.50"')
        self.assertContains(reopened, 'value="Truck 1"')

    def test_book_in_fuel_records_are_independent_per_shift(self):
        self.site_one.shift_count = 2
        self.site_one.save(update_fields=["shift_count"])
        tank = Tank.objects.create(site=self.site_one, name="Tank 1", product="Diesel")
        selected_date = timezone.localdate() - timedelta(days=2)
        url = reverse("blank-site-form", args=[self.site_one.slug, "book-in-fuel"])
        self.client.force_login(self.site_user)

        for shift, quantity in (("Day", "100.00"), ("Night", "200.00")):
            shift_url = f"{url}?date={selected_date.isoformat()}&shift={shift}"
            response = self.client.post(shift_url, {
                "business_date": selected_date.isoformat(),
                "shift_label": shift,
                "delivery_count": "1",
                f"delivery_1_tank_{tank.pk}_qty_litres": quantity,
            })
            self.assertEqual(response.status_code, 302)

        bookings = FuelBooking.objects.filter(site=self.site_one, tank=tank, business_date=selected_date)
        self.assertEqual(bookings.count(), 2)
        self.assertEqual(set(bookings.values_list("shift_label", flat=True)), {"Day", "Night"})
        self.assertEqual(
            {str(quantity) for quantity in bookings.values_list("qty_litres", flat=True)},
            {"100.00", "200.00"},
        )

    def test_book_in_fuel_adds_and_saves_multiple_deliveries_dynamically(self):
        tank = Tank.objects.create(site=self.site_one, name="Tank 1", product="Diesel")
        selected_date = timezone.localdate() - timedelta(days=1)
        url = reverse("blank-site-form", args=[self.site_one.slug, "book-in-fuel"])
        shift_url = f"{url}?date={selected_date.isoformat()}&shift=Day"
        self.client.force_login(self.site_user)

        page = self.client.get(shift_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Delivery 1")
        self.assertNotContains(page, "Delivery 2")
        self.assertContains(page, "Add another delivery")
        self.assertContains(page, 'name="delivery_count" value="1"')
        self.assertContains(page, "book_in_fuel_deliveries.js")

        response = self.client.post(shift_url, {
            "business_date": selected_date.isoformat(),
            "shift_label": "Day",
            "delivery_count": "2",
            "delivery_1_truck_reference": "Truck A",
            f"delivery_1_tank_{tank.pk}_qty_litres": "100.00",
            "delivery_2_truck_reference": "Truck B",
            f"delivery_2_tank_{tank.pk}_qty_litres": "200.00",
        })

        self.assertEqual(response.status_code, 302)
        bookings = FuelBooking.objects.filter(site=self.site_one, tank=tank, business_date=selected_date, shift_label="Day")
        self.assertEqual(bookings.count(), 2)
        self.assertEqual(
            set(bookings.values_list("delivery_number", "truck_reference", "qty_litres")),
            {(1, "Truck A", 100), (2, "Truck B", 200)},
        )
        reopened = self.client.get(url, {"date": selected_date.isoformat(), "shift": "Day"})
        self.assertContains(reopened, "Delivery 1")
        self.assertContains(reopened, "Delivery 2")
        self.assertContains(reopened, 'value="Truck A"')
        self.assertContains(reopened, 'value="Truck B"')

    def test_book_in_fuel_rejects_more_than_five_deliveries(self):
        tank = Tank.objects.create(site=self.site_one, name="Tank 1", product="Diesel")
        selected_date = timezone.localdate() - timedelta(days=1)
        url = reverse("blank-site-form", args=[self.site_one.slug, "book-in-fuel"])
        self.client.force_login(self.site_user)

        response = self.client.post(f"{url}?date={selected_date.isoformat()}&shift=Day", {
            "business_date": selected_date.isoformat(),
            "shift_label": "Day",
            "delivery_count": "6",
            "delivery_1_truck_reference": "Truck A",
            f"delivery_1_tank_{tank.pk}_qty_litres": "100.00",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ensure this value is less than or equal to 5")
        self.assertFalse(FuelBooking.objects.filter(site=self.site_one, business_date=selected_date).exists())

    def test_transfer_fuel_uses_site_tanks_and_saves_date_shift_and_audit(self):
        self.site_one.shift_count = 2
        self.site_one.save(update_fields=["shift_count"])
        source = Tank.objects.create(site=self.site_one, name="Tank 1", product="Diesel")
        destination = Tank.objects.create(site=self.site_one, name="Tank 2", product="Petrol")
        other_site_tank = Tank.objects.create(site=self.site_two, name="Other tank", product="Diesel")
        selected_date = timezone.localdate() - timedelta(days=1)
        url = reverse("blank-site-form", args=[self.site_one.slug, "transfer-fuel"])
        self.client.force_login(self.site_user)

        page = self.client.get(url, {"date": selected_date.isoformat(), "shift": "Night"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Return")
        self.assertContains(page, "From tank")
        self.assertContains(page, "To tank")
        self.assertContains(page, "Tank 1 - Diesel")
        self.assertContains(page, "Tank 2 - Petrol")
        self.assertNotContains(page, str(other_site_tank))
        self.assertContains(page, "Amount (L)")
        self.assertContains(page, "Save and Continue")
        self.assertContains(page, "Save and Exit")
        self.assertNotContains(page, "Save this form")
        self.assertContains(page, 'data-workflow-draft-key="transfer-fuel"')
        self.assertContains(page, "data-unsaved-return")

        invalid = self.client.post(f"{url}?date={selected_date.isoformat()}&shift=Night", {
            "business_date": selected_date.isoformat(),
            "shift_label": "Night",
            "from_tank": str(source.pk),
            "to_tank": str(source.pk),
            "amount_litres": "15.00",
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Choose a different destination tank.")
        self.assertEqual(FuelTransfer.objects.count(), 0)

        response = self.client.post(f"{url}?date={selected_date.isoformat()}&shift=Night", {
            "business_date": selected_date.isoformat(),
            "shift_label": "Night",
            "save_mode": "continue",
            "from_tank": str(source.pk),
            "to_tank": str(destination.pk),
            "amount_litres": "125.50",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{url}?date={selected_date.isoformat()}&shift=Night&workflow_saved=transfer-fuel")
        transfer = FuelTransfer.objects.get(site=self.site_one)
        self.assertEqual(transfer.business_date, selected_date)
        self.assertEqual(transfer.shift_label, "Night")
        self.assertEqual(transfer.from_tank, source)
        self.assertEqual(transfer.to_tank, destination)
        self.assertEqual(str(transfer.amount_litres), "125.50")
        self.assertTrue(self.site_one.audit_events.filter(category="Transfer Fuel").exists())
        exit_response = self.client.post(f"{url}?date={selected_date.isoformat()}&shift=Night", {
            "business_date": selected_date.isoformat(),
            "shift_label": "Night",
            "save_mode": "exit",
            "from_tank": str(source.pk),
            "to_tank": str(destination.pk),
            "amount_litres": "5.00",
        })
        self.assertEqual(exit_response.status_code, 302)
        self.assertEqual(exit_response["Location"], f"{reverse('site-dashboard', args=[self.site_one.slug])}?date={selected_date.isoformat()}&shift=Night&workflow_saved=transfer-fuel")
        self.assertEqual(FuelTransfer.objects.filter(site=self.site_one).count(), 2)
        history = self.client.get(reverse("site-form-history", args=[self.site_one.slug, "transfer-fuel"]), {
            "date": selected_date.isoformat(),
            "shift": "Night",
        })
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "Tank 1 - Diesel")
        self.assertContains(history, "Tank 2 - Petrol")
        self.assertContains(history, "125.50")

    def test_site_forms_and_user_management_link_to_history(self):
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        SiteGroup.objects.create(site=self.site_one, name="Test group", form_permissions=["book-in-fuel"])
        self.client.force_login(self.site_user)
        history_url = reverse("site-history", args=[self.site_one.slug])
        urls = (
            reverse("site-users", args=[self.site_one.slug]),
            reverse("site-groups", args=[self.site_one.slug]),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, history_url)
                if url == reverse("site-groups", args=[self.site_one.slug]):
                    self.assertContains(response, "selectfilter")
                    self.assertContains(response, "Granted forms")

        create_response = self.client.get(reverse("site-user-create", args=[self.site_one.slug]))
        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(create_response["Location"], reverse("site_admin:auth_user_add"))

        cash_up_url = f"{reverse('blank-site-form', args=[self.site_one.slug, 'cash-up'])}?date={timezone.localdate().isoformat()}&shift=Day"
        cash_up_response = self.client.get(cash_up_url)
        self.assertEqual(cash_up_response.status_code, 200)
        self.assertContains(cash_up_response, reverse("site-form-history", args=[self.site_one.slug, "cash-up"]))
        self.assertNotContains(cash_up_response, history_url)

    def test_site_user_and_group_creation_are_recorded_in_history(self):
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        self.client.force_login(self.site_user)

        group_response = self.client.post(reverse("site-groups", args=[self.site_one.slug]), {"name": "Shift Leads"})
        self.assertEqual(group_response.status_code, 302)
        group = SiteGroup.objects.get(site=self.site_one, name="Shift Leads")
        create_response = self.client.get(reverse("site-user-create", args=[self.site_one.slug]))
        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(create_response["Location"], reverse("site_admin:auth_user_add"))
        self.assertEqual(self.site_one.audit_events.count(), 1)
        self.assertContains(self.client.get(reverse("site-history", args=[self.site_one.slug])), "Site group")

    def test_site_user_editor_changes_only_users_at_the_current_site(self):
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        target = User.objects.create_user(username="site-target", password="initial-password")
        UserProfile.objects.create(user=target, role=UserProfile.Role.SITE, site=self.site_one)
        other_site_user = User.objects.create_user(username="other-site-target", password="initial-password")
        UserProfile.objects.create(user=other_site_user, role=UserProfile.Role.SITE, site=self.site_two)
        self.client.force_login(self.site_user)
        edit_url = reverse("site-user-edit", args=[self.site_one.slug, target.pk])

        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("site_admin:auth_user_change", args=[target.pk]))
        self.assertEqual(self.client.get(reverse("site-user-edit", args=[self.site_one.slug, other_site_user.pk])).status_code, 404)

    def test_site_user_can_access_native_user_admin_without_global_auth_perms(self):
        self.site_user.is_staff = True
        self.site_user.save(update_fields=["is_staff"])
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        self.client.force_login(self.site_user)

        self.client.get(reverse("site-admin-users", args=[self.site_one.slug]))
        response = self.client.get(reverse("site_admin:auth_user_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="groups"')
        self.assertContains(response, 'name="user_permissions"')
        self.assertContains(response, "Important dates")
        self.assertNotContains(response, "FuelLedger access")

    def test_site_user_admin_uses_native_forms_with_site_scoped_records(self):
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        site_group = SiteGroup.objects.create(site=self.site_one, name="Site one team")
        other_group = SiteGroup.objects.create(site=self.site_two, name="Site two team")
        local_user = User.objects.create_user(username="local-member", password="test-pass-123")
        UserProfile.objects.create(user=local_user, role=UserProfile.Role.SITE, site=self.site_one)
        remote_user = User.objects.create_user(username="remote-member", password="test-pass-123")
        UserProfile.objects.create(user=remote_user, role=UserProfile.Role.SITE, site=self.site_two)
        site_group.users.add(local_user)
        other_group.users.add(remote_user)
        site_group.django_group.user_set.add(local_user)
        other_group.django_group.user_set.add(remote_user)
        self.client.force_login(self.site_user)

        users_entry = self.client.get(reverse("site-admin-users", args=[self.site_one.slug]))
        self.assertEqual(users_entry.status_code, 302)
        self.assertEqual(users_entry["Location"], reverse("site_admin:auth_user_changelist"))
        users_page = self.client.get(users_entry["Location"])
        self.assertEqual(users_page.status_code, 200)
        home_response = self.client.get(reverse("site_admin:index"))
        self.assertRedirects(home_response, reverse("site-dashboard", args=[self.site_one.slug]), fetch_redirect_response=False)
        self.assertContains(users_page, "local-member")
        self.assertNotContains(users_page, "remote-member")
        self.assertEqual(self.client.get(reverse("site-admin-user-edit", args=[self.site_one.slug, remote_user.pk])).status_code, 404)
        native_user_edit = self.client.get(reverse("site_admin:auth_user_change", args=[local_user.pk]))
        self.assertEqual(native_user_edit.status_code, 200)
        self.assertContains(native_user_edit, site_group.django_group.name)
        self.assertNotContains(native_user_edit, other_group.django_group.name)
        add_user_entry = self.client.get(reverse("site-admin-user-add", args=[self.site_one.slug]))
        add_user_page = self.client.get(add_user_entry["Location"])
        self.assertEqual(add_user_page.status_code, 200)
        self.assertContains(add_user_page, 'name="password1"')
        self.assertContains(add_user_page, 'name="password2"')
        self.assertContains(add_user_page, 'name="user_permissions"')
        self.assertNotContains(add_user_page, "FuelLedger access")
        self.assertNotContains(add_user_page, "Superuser status")
        user_data = {
            "username": "django-site-member",
            "password1": "safe-test-password-123",
            "password2": "safe-test-password-123",
        }
        add_user_response = self.client.post(add_user_entry["Location"], user_data)
        self.assertEqual(add_user_response.status_code, 302)
        new_site_user = User.objects.get(username="django-site-member")
        self.assertEqual(new_site_user.fuel_profile.site, self.site_one)
        self.assertEqual(new_site_user.fuel_profile.role, UserProfile.Role.SITE)
        self.assertEqual(new_site_user.fuel_profile.form_permissions, ["book-in-fuel", "tank-reading", "mechanical-readings", "electronic-readings", "cash-up"])

        groups_entry = self.client.get(reverse("site-admin-groups", args=[self.site_one.slug]))
        self.assertEqual(groups_entry.status_code, 302)
        groups_page = self.client.get(groups_entry["Location"])
        self.assertEqual(groups_page.status_code, 200)
        self.assertContains(groups_page, site_group.django_group.name)
        self.assertNotContains(groups_page, other_group.django_group.name)
        add_group_page = self.client.get(reverse("site_admin:auth_group_add"))
        self.assertEqual(add_group_page.status_code, 200)
        self.assertContains(add_group_page, "FuelLedger site access")
        group_formset = add_group_page.context["inline_admin_formsets"][0].formset
        self.assertIn("form_permissions", group_formset.forms[0].fields)
        group_form = group_formset.forms[0]
        group_data = {
            "name": "Site one native group",
            f"{group_formset.prefix}-TOTAL_FORMS": "1",
            f"{group_formset.prefix}-INITIAL_FORMS": "0",
            f"{group_formset.prefix}-MIN_NUM_FORMS": "0",
            f"{group_formset.prefix}-MAX_NUM_FORMS": "1",
            group_form["site"].html_name: str(self.site_one.pk),
            group_form["name"].html_name: "Native operators",
            group_form["form_permissions"].html_name: ["cash-up"],
        }
        add_group_response = self.client.post(reverse("site_admin:auth_group_add"), group_data)
        self.assertEqual(add_group_response.status_code, 302)
        native_site_group = SiteGroup.objects.get(site=self.site_one, name="Native operators")
        self.assertEqual(native_site_group.form_permissions, ["cash-up"])
        self.assertEqual(native_site_group.django_group.name, f"Site {self.site_one.pk} - Native operators")
        self.assertEqual(self.client.get("/site-admin/auth/user/").status_code, 200)

    def test_site_manager_cannot_enter_native_admin_for_another_site(self):
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        self.client.force_login(self.site_user)

        response = self.client.get(reverse("site-admin-users", args=[self.site_two.slug]))

        self.assertEqual(response.status_code, 403)

    def test_site_group_grants_are_site_scoped_and_control_form_access(self):
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.form_permissions = []
        self.site_user.fuel_profile.save(update_fields=["can_manage_users", "form_permissions"])
        self.client.force_login(self.site_user)

        response = self.client.post(reverse("site-groups", args=[self.site_one.slug]), {
            "name": "Book-in operators",
            "users": [str(self.site_user.pk)],
            "form_permissions": ["book-in-fuel"],
        })

        self.assertEqual(response.status_code, 302)
        group = SiteGroup.objects.get(site=self.site_one, name="Book-in operators")
        self.assertEqual(group.form_permissions, ["book-in-fuel"])
        self.assertIn(self.site_user, group.django_group.user_set.all())
        dashboard = self.client.get(reverse("site-dashboard", args=[self.site_one.slug]), {
            "date": timezone.localdate().isoformat(),
            "shift": "Day",
        })
        self.assertContains(dashboard, "Book IN Fuel")
        self.assertNotContains(dashboard, "Tank Reading")
        self.assertNotContains(dashboard, "Cash Up")
        self.assertEqual(self.client.get(
            f"{reverse('blank-site-form', args=[self.site_one.slug, 'cash-up'])}?date={timezone.localdate().isoformat()}&shift=Day",
        ).status_code, 403)
        self.assertEqual(self.client.get(
            f"{reverse('blank-site-form', args=[self.site_one.slug, 'book-in-fuel'])}?date={timezone.localdate().isoformat()}&shift=Day",
        ).status_code, 200)

    def test_site_group_edit_updates_site_members_and_permissions(self):
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        group = SiteGroup.objects.create(site=self.site_one, name="Cash-up team", form_permissions=["cash-up"])
        self.client.force_login(self.site_user)
        edit_url = reverse("site-group-edit", args=[self.site_one.slug, group.pk])
        self.assertEqual(self.client.get(edit_url).status_code, 200)
        self.assertEqual(self.client.get(reverse("site-group-edit", args=[self.site_two.slug, group.pk])).status_code, 403)

        response = self.client.post(edit_url, {
            "name": "Fuel team",
            "users": [str(self.site_user.pk)],
            "form_permissions": ["book-in-fuel", "tank-reading"],
        })

        self.assertEqual(response.status_code, 302)
        group.refresh_from_db()
        self.assertEqual(group.name, "Fuel team")
        self.assertEqual(group.form_permissions, ["book-in-fuel", "tank-reading"])
        self.assertIn(self.site_user, group.users.all())
        self.assertTrue(self.site_one.audit_events.filter(category="Site group", object_label="Fuel team", action="changed").exists())

    def test_form_history_is_limited_to_selected_form_date_and_shift(self):
        self.client.force_login(self.site_user)
        selected_date = timezone.localdate() - timedelta(days=1)
        other_date = selected_date - timedelta(days=1)
        SiteAuditEvent.objects.create(
            site=self.site_one,
            actor=self.site_user,
            category="Book IN Fuel",
            object_label=f"{selected_date.isoformat()} / Day",
            action="saved",
            after={"reference": "TRUCK-A"},
        )
        SiteAuditEvent.objects.create(
            site=self.site_one,
            actor=self.site_user,
            category="Book IN Fuel",
            object_label=f"{other_date.isoformat()} / Day",
            action="saved",
            after={"reference": "TRUCK-OLD"},
        )
        SiteAuditEvent.objects.create(
            site=self.site_one,
            actor=self.site_user,
            category="Daily Fuel Readings",
            object_label=f"{selected_date.isoformat()} / Day",
            action="created",
            after={"dip_litres": "100.00"},
        )

        response = self.client.get(
            reverse("site-form-history", args=[self.site_one.slug, "book-in-fuel"]),
            {"date": selected_date.isoformat(), "shift": "Day"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["events"].count(), 1)
        self.assertContains(response, "TRUCK-A")
        self.assertNotContains(response, "TRUCK-OLD")
        self.assertNotContains(response, "Daily Fuel Readings")
        self.assertContains(response, "Book IN Fuel history")
        self.assertContains(response, "history-table")
        self.assertContains(response, "<th>Action</th>")
        self.assertContains(response, "Reference: TRUCK-A")
        self.assertNotContains(response, "View changes")

    def test_site_history_excludes_hq_configuration_events(self):
        self.client.force_login(self.site_user)
        SiteAuditEvent.objects.create(
            site=self.site_one,
            actor=self.hq_user,
            origin=SiteAuditEvent.Origin.HQ,
            category="Tank",
            object_label="HQ tank configuration",
            action="changed",
        )
        SiteAuditEvent.objects.create(
            site=self.site_one,
            actor=self.site_user,
            category="Book IN Fuel",
            object_label="site delivery entry",
            action="saved",
        )

        response = self.client.get(reverse("site-history", args=[self.site_one.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["events"].count(), 1)
        self.assertContains(response, "site delivery entry")
        self.assertNotContains(response, "HQ tank configuration")


class DailyFuelReadingWorkflowTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Reading Site", shift_count=2)
        self.tank = Tank.objects.create(site=self.site, name="Tank 1", product="Diesel")
        self.tank_two = Tank.objects.create(site=self.site, name="Tank 2", product="Petrol")
        self.nozzle = Nozzle.objects.create(tank=self.tank, name="Nozzle 1")
        self.nozzle_two = Nozzle.objects.create(tank=self.tank_two, name="Nozzle 2")
        self.user = User.objects.create_user(username="reader", password="test-pass-123")
        UserProfile.objects.create(user=self.user, role=UserProfile.Role.SITE, site=self.site)
        self.client = Client()
        self.client.force_login(self.user)
        self.business_date = timezone.localdate() - timedelta(days=1)

    def test_reading_form_has_four_tabs_with_the_current_form_selected(self):
        response = self.client.get(
            reverse("fuel-reading-entry", args=[self.site.slug, "tank-reading"]),
            {"date": self.business_date.isoformat(), "shift": "Night"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fuel Readings")
        self.assertContains(response, 'aria-selected="true"')
        self.assertContains(response, "Return")
        self.assertContains(response, "History")
        self.assertContains(response, "Save and Continue")
        self.assertContains(response, "Save and Exit")
        self.assertContains(response, 'data-workflow-draft-key="tank-reading"')
        self.assertContains(response, "data-unsaved-return")
        self.assertLess(response.content.decode().index("fuel-workflow-actions"), response.content.decode().index("fuel-reading-tabs"))
        for reading_type in ("tank-reading", "fuel-sales", "mechanical-readings", "electronic-readings"):
            self.assertContains(response, reverse("fuel-reading-entry", args=[self.site.slug, reading_type]))

    def test_site_reading_selection_filters_tabs_entry_and_fuel_report(self):
        self.site.use_tank_readings = False
        self.site.use_mechanical_readings = False
        self.site.use_electronic_readings = False
        self.site.save(update_fields=["use_tank_readings", "use_mechanical_readings", "use_electronic_readings"])
        reading = DailyFuelReading.objects.create(
            site=self.site,
            business_date=self.business_date,
            shift_label="Night",
            created_by=self.user,
            updated_by=self.user,
            fuel_sales={"Diesel": "42.50"},
        )
        TankDipReading.objects.create(daily_reading=reading, tank=self.tank, dip_litres="1000.00")
        NozzleFuelReading.objects.create(
            daily_reading=reading,
            nozzle=self.nozzle,
            mechanical_reading="1200.00",
            electronic_reading="1150.00",
        )

        dashboard = self.client.get(reverse("site-dashboard", args=[self.site.slug]), {
            "date": self.business_date.isoformat(),
            "shift": "Night",
        })
        self.assertContains(dashboard, reverse("fuel-reading-entry", args=[self.site.slug, "fuel-sales"]))
        for reading_type in ("tank-reading", "mechanical-readings", "electronic-readings"):
            self.assertNotContains(dashboard, reverse("fuel-reading-entry", args=[self.site.slug, reading_type]))
            self.assertEqual(
                self.client.get(reverse("fuel-reading-entry", args=[self.site.slug, reading_type]), {
                    "date": self.business_date.isoformat(),
                    "shift": "Night",
                }).status_code,
                404,
            )

        report = self.client.get(reverse("fuel-reports", args=[self.site.slug]), {
            "period": "day",
            "from_date": self.business_date.isoformat(),
        })
        self.assertEqual(report.status_code, 200)
        self.assertEqual(len(report.context["product_rows"]), 1)
        row = report.context["product_rows"][0]
        self.assertEqual(row["fuel_sales"], Decimal("42.50"))
        self.assertIsNone(row["tank_decrease"])
        self.assertIsNone(row["mechanical"])
        self.assertIsNone(row["electronic"])
        self.assertNotContains(report, "Tank decrease")
        self.assertNotContains(report, "Mechanical - Sales")

        self.site.use_fuel_sales = False
        self.site.save(update_fields=["use_fuel_sales"])
        empty_dashboard = self.client.get(reverse("site-dashboard", args=[self.site.slug]), {
            "date": self.business_date.isoformat(),
            "shift": "Night",
        })
        self.assertNotContains(empty_dashboard, "Fuel Readings")
        self.assertEqual(
            self.client.get(reverse("fuel-reports", args=[self.site.slug]), {
                "period": "day",
                "from_date": self.business_date.isoformat(),
            }).status_code,
            403,
        )

    def test_configured_shifts_drive_entry_and_reading_history(self):
        self.tank.capacity_litres = "25000.00"
        self.tank.save(update_fields=["capacity_litres"])
        entry_query = {"date": self.business_date.isoformat(), "shift": "Night"}
        dashboard = self.client.get(reverse("site-dashboard", args=[self.site.slug]), entry_query)
        self.assertEqual(dashboard.status_code, 200)
        for reading_type in ("tank-reading", "fuel-sales", "mechanical-readings", "electronic-readings"):
            self.assertContains(dashboard, reverse("fuel-reading-entry", args=[self.site.slug, reading_type]))
        fuel_reports_path = reverse("fuel-reports", args=[self.site.slug])
        self.assertContains(dashboard, fuel_reports_path)
        self.assertEqual(dashboard.content.decode().count(fuel_reports_path), 1)
        self.assertEqual(
            self.client.get(reverse("fuel-reading-entry", args=[self.site.slug, "tank-reading"]), {
                "date": self.business_date.isoformat(),
                "shift": "Morning",
            }).status_code,
            302,
        )

        tank_url = reverse("fuel-reading-entry", args=[self.site.slug, "tank-reading"])
        tank_post_url = f"{tank_url}?date={self.business_date.isoformat()}&shift=Night"
        tank_page = self.client.get(tank_url, entry_query)
        self.assertContains(tank_page, self.tank.name)
        self.assertContains(tank_page, self.tank_two.name)
        self.assertContains(tank_page, "Tank capacity (L)")
        self.assertContains(tank_page, 'value="25000.00" disabled')
        self.assertContains(tank_page, "Depth (cm)")
        self.assertNotContains(tank_page, self.nozzle.name)
        tank_response = self.client.post(tank_post_url, {
            "business_date": self.business_date.isoformat(),
            "shift_label": "Night",
            f"tank_{self.tank.pk}_dip": "1234.50",
            f"tank_{self.tank.pk}_depth_cm": "112.25",
            f"tank_{self.tank_two.pk}_dip": "987.65",
            f"tank_{self.tank_two.pk}_depth_cm": "88.50",
            "save_mode": "continue",
        })
        self.assertRedirects(
            tank_response,
            f"{tank_url}?date={self.business_date.isoformat()}&shift=Night&workflow_saved=tank-reading",
        )
        tank_reading = TankDipReading.objects.get(daily_reading__site=self.site, daily_reading__business_date=self.business_date, tank=self.tank)
        self.assertEqual(str(tank_reading.depth_cm), "112.25")
        reopened_tank_page = self.client.get(tank_url, entry_query)
        self.assertContains(reopened_tank_page, 'value="112.25"')

        duplicate_diesel_tank = Tank.objects.create(site=self.site, name="Tank 3", product=" diesel ")
        Tank.objects.create(site=self.site, name="Tank 4", product="Petrol 95")
        Tank.objects.create(site=self.site, name="Tank 5", product="Petrol 93")
        Tank.objects.create(site=self.site, name="Tank 6", product="Diesel 50 PPM")
        Tank.objects.create(site=self.site, name="Tank 7", product="Diesel 500PPM")
        fuel_sales_url = reverse("fuel-reading-entry", args=[self.site.slug, "fuel-sales"])
        fuel_sales_page = self.client.get(fuel_sales_url, entry_query)
        self.assertContains(fuel_sales_page, "Fuel sales (L)")
        self.assertContains(fuel_sales_page, "Diesel")
        self.assertContains(fuel_sales_page, "Petrol")
        self.assertEqual(fuel_sales_page.content.decode().count('name="fuel_sales_product_1"'), 1)
        for index in range(2, 7):
            self.assertEqual(fuel_sales_page.content.decode().count(f'name="fuel_sales_product_{index}"'), 1)
        for product in ("Petrol 95", "Petrol 93", "Diesel 50 PPM", "Diesel 500PPM"):
            self.assertContains(fuel_sales_page, product)
        self.assertNotContains(fuel_sales_page, duplicate_diesel_tank.name)
        fuel_sales_response = self.client.post(fuel_sales_url + f"?date={self.business_date.isoformat()}&shift=Night", {
            "business_date": self.business_date.isoformat(),
            "shift_label": "Night",
            "fuel_sales_product_1": "123.45",
            "fuel_sales_product_2": "67.89",
            "fuel_sales_product_3": "10.00",
            "fuel_sales_product_4": "11.00",
            "fuel_sales_product_5": "12.00",
            "fuel_sales_product_6": "13.00",
            "save_mode": "continue",
        })
        self.assertRedirects(
            fuel_sales_response,
            f"{fuel_sales_url}?date={self.business_date.isoformat()}&shift=Night&workflow_saved=fuel-sales",
        )
        reading = DailyFuelReading.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(reading.fuel_sales, {
            "Diesel": "123.45",
            "Petrol": "67.89",
            "Petrol 95": "10.00",
            "Petrol 93": "11.00",
            "Diesel 50 PPM": "12.00",
            "Diesel 500PPM": "13.00",
        })
        reopened_fuel_sales = self.client.get(fuel_sales_url, entry_query)
        self.assertContains(reopened_fuel_sales, 'value="123.45"')
        self.assertContains(reopened_fuel_sales, 'value="67.89"')

        mechanical_url = reverse("fuel-reading-entry", args=[self.site.slug, "mechanical-readings"])
        mechanical_post_url = f"{mechanical_url}?date={self.business_date.isoformat()}&shift=Night"
        mechanical_page = self.client.get(mechanical_url, entry_query)
        self.assertContains(mechanical_page, self.nozzle.name)
        self.assertContains(mechanical_page, self.nozzle_two.name)
        self.assertContains(mechanical_page, self.tank_two.name)
        self.assertContains(mechanical_page, "reading-table--compact-inputs")
        self.assertContains(mechanical_page, "reading-nozzle-number")
        self.assertLess(mechanical_page.content.decode().index("Nozzle</th>"), mechanical_page.content.decode().index("Previous</th>"))
        mechanical_response = self.client.post(mechanical_post_url, {
            "business_date": self.business_date.isoformat(),
            "shift_label": "Night",
            "save_mode": "continue",
            f"nozzle_{self.nozzle.pk}_mechanical": "234.56",
            f"nozzle_{self.nozzle_two.pk}_mechanical": "456.78",
        })
        self.assertRedirects(mechanical_response, f"{mechanical_post_url}&workflow_saved=mechanical-readings", fetch_redirect_response=False)

        electronic_url = reverse("fuel-reading-entry", args=[self.site.slug, "electronic-readings"])
        electronic_post_url = f"{electronic_url}?date={self.business_date.isoformat()}&shift=Night"
        electronic_page = self.client.get(electronic_url, entry_query)
        self.assertContains(electronic_page, self.nozzle.name)
        self.assertContains(electronic_page, self.nozzle_two.name)
        self.assertContains(electronic_page, "reading-table--compact-inputs")
        self.assertContains(fuel_sales_page, "reading-table--compact-inputs")
        electronic_response = self.client.post(electronic_post_url, {
            "business_date": self.business_date.isoformat(),
            "shift_label": "Night",
            "save_mode": "continue",
            f"nozzle_{self.nozzle.pk}_electronic": "345.67",
            f"nozzle_{self.nozzle_two.pk}_electronic": "567.89",
        })
        self.assertRedirects(electronic_response, f"{electronic_post_url}&workflow_saved=electronic-readings", fetch_redirect_response=False)

        reading = DailyFuelReading.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(str(TankDipReading.objects.get(daily_reading=reading, tank=self.tank).dip_litres), "1234.50")
        self.assertEqual(str(TankDipReading.objects.get(daily_reading=reading, tank=self.tank_two).dip_litres), "987.65")
        nozzle_reading = NozzleFuelReading.objects.get(daily_reading=reading, nozzle=self.nozzle)
        self.assertEqual(str(nozzle_reading.mechanical_reading), "234.56")
        self.assertEqual(str(nozzle_reading.electronic_reading), "345.67")
        self.assertEqual(self.site.audit_events.filter(category="Tank Reading").count(), 1)
        self.assertEqual(self.site.audit_events.filter(category="Mechanical Readings").count(), 1)
        self.assertEqual(self.site.audit_events.filter(category="Electronic Readings").count(), 1)
        for reading_type, label in (("tank-reading", "Tank Reading"), ("fuel-sales", "Fuel Sales"), ("mechanical-readings", "Mechanical Readings"), ("electronic-readings", "Electronic Readings")):
            history = self.client.get(reverse("site-form-history", args=[self.site.slug, reading_type]), entry_query)
            self.assertContains(history, f"{label} history")
            self.assertEqual(history.context["events"].count(), 1)

    def test_mechanical_and_electronic_nozzles_are_globally_numeric_sorted(self):
        Nozzle.objects.create(tank=self.tank, name="Nozzle 10")
        Nozzle.objects.create(tank=self.tank_two, name="Nozzle 3")
        for reading_type in ("mechanical-readings", "electronic-readings"):
            response = self.client.get(
                reverse("fuel-reading-entry", args=[self.site.slug, reading_type]),
                {"date": self.business_date.isoformat(), "shift": "Night"},
            )
            html = response.content.decode()
            positions = [html.index(f">{name}</td>") for name in ("Nozzle 1", "Nozzle 2", "Nozzle 3", "Nozzle 10")]
            self.assertEqual(positions, sorted(positions))

    def test_fuel_readings_show_previous_shift_values_and_differences(self):
        self.site.shift_count = 3
        self.site.save(update_fields=["shift_count"])
        previous_date = self.business_date - timedelta(days=1)
        previous_reading = DailyFuelReading.objects.create(
            site=self.site,
            business_date=previous_date,
            shift_label="Evening",
            created_by=self.user,
            updated_by=self.user,
        )
        TankDipReading.objects.create(daily_reading=previous_reading, tank=self.tank, dip_litres="800.00")
        NozzleFuelReading.objects.create(
            daily_reading=previous_reading,
            nozzle=self.nozzle,
            mechanical_reading="1000.00",
            electronic_reading="700.00",
        )
        current_reading = DailyFuelReading.objects.create(
            site=self.site,
            business_date=self.business_date,
            shift_label="Morning",
            created_by=self.user,
            updated_by=self.user,
        )
        TankDipReading.objects.create(daily_reading=current_reading, tank=self.tank, dip_litres="900.00")
        NozzleFuelReading.objects.create(
            daily_reading=current_reading,
            nozzle=self.nozzle,
            mechanical_reading="1200.00",
            electronic_reading="900.00",
        )
        afternoon_reading = DailyFuelReading.objects.create(
            site=self.site,
            business_date=self.business_date,
            shift_label="Afternoon",
            created_by=self.user,
            updated_by=self.user,
        )
        NozzleFuelReading.objects.create(
            daily_reading=afternoon_reading,
            nozzle=self.nozzle,
            mechanical_reading="1400.00",
            electronic_reading="1100.00",
        )

        tank_url = reverse("fuel-reading-entry", args=[self.site.slug, "tank-reading"])
        tank_page = self.client.get(tank_url, {"date": self.business_date.isoformat(), "shift": "Morning"})
        self.assertContains(tank_page, 'value="800.00" readonly class="previous-reading-field"')
        self.assertContains(tank_page, 'value="-100.00" readonly class="reading-difference-field"')
        self.assertContains(tank_page, 'name="tank_')

        mechanical_url = reverse("fuel-reading-entry", args=[self.site.slug, "mechanical-readings"])
        mechanical_page = self.client.get(mechanical_url, {"date": self.business_date.isoformat(), "shift": "Afternoon"})
        self.assertContains(mechanical_page, 'value="1200.00"')
        self.assertContains(mechanical_page, 'value="-200.00" readonly class="reading-difference-field"')

        electronic_url = reverse("fuel-reading-entry", args=[self.site.slug, "electronic-readings"])
        electronic_page = self.client.get(electronic_url, {"date": self.business_date.isoformat(), "shift": "Afternoon"})
        self.assertContains(electronic_page, 'value="900.00"')
        self.assertContains(electronic_page, 'value="-200.00" readonly class="reading-difference-field"')

    def test_fuel_reports_reconcile_tank_sales_and_nozzle_deltas_by_product_and_shift(self):
        self.site.shift_count = 2
        self.site.save(update_fields=["shift_count"])
        previous_date = self.business_date - timedelta(days=1)
        duplicate_diesel_tank = Tank.objects.create(site=self.site, name="Tank 3", product="Diesel")
        duplicate_diesel_nozzle = Nozzle.objects.create(tank=duplicate_diesel_tank, name="Nozzle 3")

        def save_reading(business_date, shift_label, tank_dips, nozzle_values, fuel_sales):
            reading = DailyFuelReading.objects.create(
                site=self.site,
                business_date=business_date,
                shift_label=shift_label,
                created_by=self.user,
                updated_by=self.user,
                fuel_sales=fuel_sales,
            )
            for tank, dip in zip((self.tank, self.tank_two, duplicate_diesel_tank), tank_dips):
                TankDipReading.objects.create(daily_reading=reading, tank=tank, dip_litres=dip)
            for nozzle, values in zip((self.nozzle, self.nozzle_two, duplicate_diesel_nozzle), nozzle_values):
                NozzleFuelReading.objects.create(
                    daily_reading=reading,
                    nozzle=nozzle,
                    mechanical_reading=values[0],
                    electronic_reading=values[1],
                )

        save_reading(previous_date, "Night", ("1000.00", "2000.00", "5000.00"), (("1000.00", "900.00"), ("2000.00", "1900.00"), ("5000.00", "4500.00")), {})
        save_reading(self.business_date, "Day", ("950.00", "1950.00", "4950.00"), (("1050.00", "950.00"), ("2050.00", "1950.00"), ("5050.00", "4550.00")), {"Diesel": "100.00", "Petrol": "50.00"})
        save_reading(self.business_date, "Night", ("900.00", "1900.00", "4900.00"), (("1100.00", "1000.00"), ("2100.00", "2000.00"), ("5100.00", "4600.00")), {"Diesel": "100.00", "Petrol": "50.00"})

        report_url = reverse("fuel-reports", args=[self.site.slug])
        report = self.client.get(report_url, {"period": "day", "from_date": self.business_date.isoformat()})
        self.assertEqual(report.status_code, 200)
        self.assertContains(report, 'name="from_date"')
        self.assertContains(report, 'name="to_date"')
        self.assertNotContains(report, "Reference date")
        self.assertEqual(report.context["report_start"], self.business_date)
        self.assertEqual(report.context["report_end"], self.business_date)
        self.assertEqual(len(report.context["product_rows"]), 2)
        self.assertEqual(len(report.context["detail_rows"]), 4)
        for product in report.context["product_rows"]:
            expected_total = Decimal("200.00") if product["product"] == "Diesel" else Decimal("100.00")
            self.assertEqual(product["tank_decrease"], expected_total)
            self.assertEqual(product["fuel_sales"], expected_total)
            self.assertEqual(product["mechanical"], expected_total)
            self.assertEqual(product["electronic"], expected_total)
            for check in ("tank_vs_sales", "mechanical_vs_sales", "electronic_vs_sales", "mechanical_vs_electronic", "tank_vs_mechanical", "tank_vs_electronic"):
                self.assertEqual(product[check], Decimal("0.00"))

        night_report = self.client.get(report_url, {
            "period": "day",
            "from_date": self.business_date.isoformat(),
            "shift": "Night",
        })
        self.assertEqual(len(night_report.context["detail_rows"]), 2)
        self.assertEqual([row["shift"] for row in night_report.context["detail_rows"]], ["Night", "Night"])
        self.assertTrue(all(row["tank_vs_sales"] == 0 for row in night_report.context["detail_rows"]))


class CashUpWorkflowTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Cash Up Site", currency="ZAR", shift_count=2)
        self.user = User.objects.create_user(username="cashup-user", password="test-pass-123")
        UserProfile.objects.create(user=self.user, role=UserProfile.Role.SITE, site=self.site)
        self.client = Client()
        self.client.force_login(self.user)
        self.business_date = timezone.localdate() - timedelta(days=1)

    def cash_up_url(self, section):
        return reverse("cash-up-entry", args=[self.site.slug, section])

    def cash_up_query(self):
        return {"date": self.business_date.isoformat(), "shift": "Night"}

    def test_non_airtime_tab_skips_airtime_history_lookup(self):
        with patch("ledger.views._previous_airtime_balance") as airtime_balance_lookup:
            response = self.client.get(self.cash_up_url("cash"), self.cash_up_query())

        self.assertEqual(response.status_code, 200)
        airtime_balance_lookup.assert_not_called()

    def test_cash_up_tabs_are_shown_on_dashboard_and_entry_page(self):
        CashUpAdditional.objects.create(site=self.site, additional_field="Till variance")
        dashboard = self.client.get(
            reverse("site-dashboard", args=[self.site.slug]),
            self.cash_up_query(),
        )
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, reverse("cash-up-reports", args=[self.site.slug]))
        cash_page = self.client.get(self.cash_up_url("cash"), self.cash_up_query())
        self.assertEqual(cash_page.status_code, 200)
        for section, label in (
            ("cash", "Cash"),
            ("vouchers", "Vouchers"),
            ("airtime", "Airtime"),
            ("sales-account", "Account"),
            ("fuel", "Fuel"),
            ("payouts", "Payouts"),
            ("eft-driveway", "EFT"),
            ("manual-shop", "Manual"),
            ("cash-up-additional", "Additional"),
            ("summary", "Summary"),
        ):
            self.assertContains(dashboard, self.cash_up_url(section))
            self.assertContains(cash_page, label)
        self.assertContains(cash_page, "Coins")
        self.assertContains(cash_page, "Safedrops")
        self.assertContains(cash_page, 'class="currency-prefix">ZAR</span>')
        self.assertContains(cash_page, "js/cash_up.js")
        self.assertNotContains(cash_page, "<h2>Cash</h2>")
        self.assertContains(cash_page, "Save and Continue")
        self.assertContains(cash_page, "Save and Exit")
        self.assertContains(cash_page, ">Return</a>")
        self.assertEqual(cash_page.content.decode().count('value="continue"'), 1)
        self.assertEqual(cash_page.content.decode().count('value="exit"'), 1)
        self.assertNotContains(cash_page, "Save this tab")
        self.assertEqual(cash_page.content.decode().count('value="tab"'), 0)
        cash_page_html = cash_page.content.decode()
        self.assertLess(cash_page_html.index("cash-up-actions--top"), cash_page_html.index('aria-label="Cash up sections"'))
        self.assertContains(cash_page, "cash-up-page-heading")
        self.assertContains(cash_page, "cash-up-form")

    def test_cash_up_summary_reconciles_saved_shift_totals_in_site_currency(self):
        additional = CashUpAdditional.objects.create(site=self.site, additional_field="Till variance")
        CashUpShiftRecord.objects.create(
            site=self.site,
            business_date=self.business_date,
            shift_label="Night",
            created_by=self.user,
            values={
                "coins": "30.00",
                "safedrops": "20.00",
                "voucher_1": "6.00",
                "voucher_2": "4.00",
                "sales_account_1": "20.00",
                "arrears_account_1": "10.00",
                "forecourt_total": "130.00",
                "shop_total": "0.00",
                "payout_1": "2.00",
                "eft_1": "15.00",
                "manual_1": "7.00",
                f"additional_{additional.pk}": "8.00",
                "airtime_slip_1": "3.00",
                "airtime_slip_2": "4.00",
            },
        )

        response = self.client.get(self.cash_up_url("summary"), self.cash_up_query())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total Cash")
        self.assertContains(response, "Total Vouchers")
        self.assertContains(response, "Combined Sales and Arrears")
        self.assertContains(response, "Total EFT")
        self.assertContains(response, "Total Manual")
        self.assertContains(response, "Total Additional")
        self.assertContains(response, "Total Airtime Slip")
        self.assertContains(response, "Total Payouts")
        self.assertContains(response, "cash-up-summary-row--payouts")
        self.assertContains(response, "Total Amount Accounted For")
        self.assertContains(response, "Total Sales")
        self.assertContains(response, "Difference (Accounted For - Total Sales)")
        self.assertContains(response, "129.00")
        self.assertContains(response, "130.00")
        self.assertContains(response, "-1.00")
        self.assertEqual(response.content.decode().count('class="currency-prefix">ZAR</span>'), 11)
        self.assertNotContains(response, "Save and Continue")

    def test_additional_tab_and_summary_amount_follow_site_registration(self):
        record = CashUpShiftRecord.objects.create(
            site=self.site,
            business_date=self.business_date,
            shift_label="Night",
            created_by=self.user,
            values={"forecourt_total": "100.00", "additional_999": "50.00"},
        )
        dashboard = self.client.get(reverse("site-dashboard", args=[self.site.slug]), self.cash_up_query())
        cash_page = self.client.get(self.cash_up_url("cash"), self.cash_up_query())
        summary_page = self.client.get(self.cash_up_url("summary"), self.cash_up_query())
        self.assertNotContains(dashboard, self.cash_up_url("cash-up-additional"))
        self.assertNotContains(cash_page, ">Additional</a>")
        self.assertNotContains(summary_page, "Total Additional")
        self.assertNotContains(summary_page, "50.00")

        additional = CashUpAdditional.objects.create(site=self.site, additional_field="Till variance")
        record.values[f"additional_{additional.pk}"] = "8.00"
        record.save(update_fields=["values", "updated_at"])
        dashboard = self.client.get(reverse("site-dashboard", args=[self.site.slug]), self.cash_up_query())
        cash_page = self.client.get(self.cash_up_url("cash"), self.cash_up_query())
        summary_page = self.client.get(self.cash_up_url("summary"), self.cash_up_query())
        self.assertContains(dashboard, self.cash_up_url("cash-up-additional"))
        self.assertContains(cash_page, ">Additional</a>")
        self.assertContains(summary_page, "Total Additional")
        self.assertContains(summary_page, "8.00")
        self.assertContains(summary_page, "-92.00")

        additional.delete()
        dashboard = self.client.get(reverse("site-dashboard", args=[self.site.slug]), self.cash_up_query())
        cash_page = self.client.get(self.cash_up_url("cash"), self.cash_up_query())
        summary_page = self.client.get(self.cash_up_url("summary"), self.cash_up_query())
        self.assertNotContains(dashboard, self.cash_up_url("cash-up-additional"))
        self.assertNotContains(cash_page, ">Additional</a>")
        self.assertNotContains(summary_page, "Total Additional")
        self.assertNotContains(summary_page, "8.00")
        self.assertContains(summary_page, "100.00")

    def test_cash_up_reports_roll_up_periods_and_filter_by_shift(self):
        week_start = self.business_date - timedelta(days=self.business_date.weekday())
        anchor_date = self.business_date + timedelta(days=1)
        additional = CashUpAdditional.objects.create(site=self.site, additional_field="Till variance")
        records = (
            (week_start, "Day", {"coins": "10.00", "forecourt_total": "100.00", "airtime_slip_1": "5.00", f"additional_{additional.pk}": "2.00"}),
            (week_start, "Night", {"coins": "20.00", "forecourt_total": "200.00", "payout_1": "5.00"}),
            (week_start + timedelta(days=1), "Night", {"coins": "30.00", "forecourt_total": "300.00"}),
        )
        for business_date, shift_label, values in records:
            CashUpShiftRecord.objects.create(
                site=self.site,
                business_date=business_date,
                shift_label=shift_label,
                created_by=self.user,
                values=values,
            )

        report_url = reverse("cash-up-reports", args=[self.site.slug])
        week_report = self.client.get(report_url, {"period": "week", "from_date": anchor_date.isoformat()})
        self.assertEqual(week_report.status_code, 200)
        self.assertContains(week_report, 'name="from_date"')
        self.assertContains(week_report, 'name="to_date"')
        self.assertNotContains(week_report, "Reference date")
        self.assertEqual(week_report.context["report_start"], anchor_date - timedelta(days=7))
        self.assertEqual(week_report.context["report_end"], anchor_date)
        self.assertEqual(week_report.context["summary_accounted_total"], Decimal("72.00"))
        self.assertEqual(week_report.context["summary_total_sales"], Decimal("600.00"))
        self.assertEqual(week_report.context["summary_difference"], Decimal("-528.00"))
        self.assertEqual(len(week_report.context["report_rows"]), 3)
        self.assertEqual([row["shift"] for row in week_report.context["report_rows"]], ["Day", "Night", "Night"])

        day_report = self.client.get(report_url, {"period": "week", "from_date": anchor_date.isoformat(), "shift": "Day"})
        self.assertEqual(day_report.context["summary_accounted_total"], Decimal("17.00"))
        self.assertEqual(day_report.context["summary_total_sales"], Decimal("100.00"))
        self.assertEqual(len(day_report.context["report_rows"]), 1)

        manual_report = self.client.get(report_url, {
            "period": "manual",
            "from_date": week_start.isoformat(),
            "to_date": week_start.isoformat(),
        })
        self.assertEqual(manual_report.context["summary_accounted_total"], Decimal("42.00"))
        self.assertEqual(manual_report.context["summary_total_sales"], Decimal("300.00"))
        self.assertEqual(len(manual_report.context["report_rows"]), 2)
        previous_month = anchor_date.month - 1 or 12
        previous_month_year = anchor_date.year - (1 if anchor_date.month == 1 else 0)
        previous_month_date = date(
            previous_month_year,
            previous_month,
            min(anchor_date.day, calendar.monthrange(previous_month_year, previous_month)[1]),
        )

        for period, expected_start, expected_end in (
            ("day", anchor_date, anchor_date),
            ("month", previous_month_date, anchor_date),
            ("year", anchor_date.replace(year=anchor_date.year - 1), anchor_date),
        ):
            response = self.client.get(report_url, {"period": period, "from_date": anchor_date.isoformat()})
            self.assertEqual(response.context["report_start"], expected_start)
            self.assertEqual(response.context["report_end"], expected_end)

    def test_airtime_save_and_continue_opens_summary_tab(self):
        response = self.client.post(
            f"{self.cash_up_url('airtime')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "airtime_shift_nr": "SHIFT-1",
                "airtime_balance_1": "100.00",
                "save_mode": "continue",
            },
        )

        self.assertRedirects(
            response,
            f"{self.cash_up_url('summary')}?date={self.business_date.isoformat()}&shift=Night&saved=airtime",
        )

    def test_cash_coins_and_safedrops_are_saved_and_totalled(self):
        response = self.client.post(
            f"{self.cash_up_url('cash')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "coins": "10.25",
                "safedrops": "2.50",
            },
        )
        self.assertEqual(response.status_code, 302)
        record = CashUpShiftRecord.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(record.values["coins"], "10.25")
        self.assertEqual(record.values["safedrops"], "2.50")
        reopened = self.client.get(self.cash_up_url("cash"), self.cash_up_query())
        self.assertContains(reopened, "12.75")
        self.assertContains(reopened, 'value="10.25"')

    def test_fuel_tab_uses_site_tanks_and_saves_a_currency_total(self):
        tank_one = Tank.objects.create(site=self.site, name="Tank 1", product="Diesel")
        tank_two = Tank.objects.create(site=self.site, name="Tank 2", product=" diesel ")
        tank_three = Tank.objects.create(site=self.site, name="Tank 3", product="Petrol")
        other_site = Site.objects.create(name="Other Fuel Site")
        Tank.objects.create(site=other_site, name="Other Tank", product="Diesel")
        page = self.client.get(self.cash_up_url("fuel"), self.cash_up_query())
        self.assertEqual(page.status_code, 200)
        fuel_fields = {label: key for key, label in page.context["form"].entry_fields}
        self.assertEqual(set(fuel_fields), {"Diesel", "Petrol"})
        self.assertNotContains(page, "Tank 1")
        self.assertNotContains(page, "Tank 2")
        self.assertNotContains(page, "Other Tank")
        self.assertContains(page, "Total Fuel")
        self.assertEqual(page.content.decode().count('class="currency-prefix">ZAR</span>'), 3)
        response = self.client.post(
            f"{self.cash_up_url('fuel')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                fuel_fields["Diesel"]: "12.50",
                fuel_fields["Petrol"]: "25.25",
            },
        )
        self.assertEqual(response.status_code, 302)
        record = CashUpShiftRecord.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(record.values[fuel_fields["Diesel"]], "12.50")
        self.assertEqual(record.values[fuel_fields["Petrol"]], "25.25")
        self.assertEqual(record.values["totals"]["fuel"], "37.75")
        reopened = self.client.get(self.cash_up_url("fuel"), self.cash_up_query())
        self.assertContains(reopened, "37.75")

    def test_vouchers_and_payouts_accept_multiple_rows_and_store_shift_totals(self):
        for section, prefix, count_key, values, expected_total in (
            ("vouchers", "voucher", "voucher_count", ("5.25", "7.00"), "12.25"),
            ("payouts", "payout", "payout_count", ("2.00", "3.50"), "5.50"),
        ):
            page = self.client.get(self.cash_up_url(section), self.cash_up_query())
            self.assertContains(page, f'data-add-repeat="{section}"')
            self.assertContains(page, 'max="100000000"')
            post_data = {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                count_key: "2",
                f"{prefix}_1": values[0],
                f"{prefix}_2": values[1],
            }
            response = self.client.post(
                f"{self.cash_up_url(section)}?date={self.business_date.isoformat()}&shift=Night",
                post_data,
            )
            self.assertEqual(response.status_code, 302)
            record = CashUpShiftRecord.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
            self.assertEqual(record.values["totals"][section], expected_total)
            reopened = self.client.get(self.cash_up_url(section), self.cash_up_query())
            self.assertContains(reopened, expected_total)
            self.assertContains(reopened, f'name="{prefix}_2"')

    def test_money_fields_reject_amounts_over_one_hundred_million(self):
        response = self.client.post(
            f"{self.cash_up_url('cash')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "coins": "100000000.01",
                "safedrops": "0",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ensure this value is less than or equal to 100000000.")
        self.assertFalse(CashUpShiftRecord.objects.filter(site=self.site, business_date=self.business_date).exists())

    def test_airtime_shift_number_has_no_currency_and_amounts_use_site_currency(self):
        response = self.client.get(self.cash_up_url("airtime"), self.cash_up_query())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Airtime Shift Number")
        self.assertContains(response, "Airtime Slip")
        self.assertContains(response, "Airtime System")
        self.assertContains(response, "Airtime Balance")
        self.assertContains(response, "Airtime Loaded")
        self.assertContains(response, 'maxlength="10"')
        self.assertContains(response, 'class="airtime-shift-number-input"')
        self.assertContains(response, "Difference")
        first_balance = re.search(r'<input[^>]*name="airtime_balance_1"[^>]*>', response.content.decode()).group()
        self.assertNotIn("readonly", first_balance)
        self.assertEqual(response.content.decode().count('class="currency-prefix">ZAR</span>'), 5)
        self.assertEqual(self.client.post(
            f"{self.cash_up_url('airtime')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "airtime_shift_nr": "SHIFT-9",
                "airtime_slip": "8.50",
            },
        ).status_code, 302)
        record = CashUpShiftRecord.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(record.values["airtime_shift_nr"], "SHIFT-9")
        self.assertEqual(record.values["airtime_slip"], "8.50")
        self.assertNotContains(response, "Total Airtime")
        self.assertNotContains(response, "Total Airtime Loaded")

    def test_airtime_first_balance_is_manual_and_later_balances_chain(self):
        CashUpShiftRecord.objects.create(
            site=self.site,
            business_date=self.business_date,
            shift_label="Day",
            created_by=self.user,
            values={"airtime_balance": "100.00"},
        )

        response = self.client.get(self.cash_up_url("airtime"), self.cash_up_query())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-airtime-previous-balance="100.00"')
        first_balance = re.search(r'<input[^>]*name="airtime_balance_1"[^>]*>', response.content.decode()).group()
        first_slip = re.search(r'<input[^>]*name="airtime_slip_1"[^>]*>', response.content.decode()).group()
        self.assertIn("readonly", first_balance)
        self.assertNotIn("readonly", first_slip)

        post_response = self.client.post(
            f"{self.cash_up_url('airtime')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "airtime_count": "2",
                "airtime_balance_1": "110.00",
                "airtime_slip_1": "20.00",
                "airtime_loaded_1": "5.00",
                "airtime_slip_2": "10.00",
                "airtime_loaded_2": "2.00",
            },
        )
        self.assertEqual(post_response.status_code, 302)
        record = CashUpShiftRecord.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(record.values["airtime_balance_1"], "85.00")
        self.assertEqual(record.values["airtime_balance_2"], "77.00")

        response = self.client.get(self.cash_up_url("airtime"), self.cash_up_query())
        html = response.content.decode()
        second_balance = re.search(r'<input[^>]*name="airtime_balance_2"[^>]*>', html).group()
        self.assertIn("readonly", second_balance)
        self.assertIn("airtime-balance-readonly", second_balance)

        post_response = self.client.post(
            f"{self.cash_up_url('airtime')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "airtime_count": "2",
                "airtime_balance_1": "110.00",
                "airtime_slip_1": "20.00",
                "airtime_loaded_1": "5.00",
            },
        )
        self.assertEqual(post_response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.values["airtime_balance_2"], "85.00")

    def test_airtime_starting_balance_follows_one_two_and_three_shift_order(self):
        previous_date = self.business_date - timedelta(days=1)

        def save_record(business_date, shift_label, balance_values):
            CashUpShiftRecord.objects.create(
                site=self.site,
                business_date=business_date,
                shift_label=shift_label,
                created_by=self.user,
                values=balance_values,
            )

        def assert_starting_balance(shift_label, expected, displayed=None):
            response = self.client.get(
                self.cash_up_url("airtime"),
                {"date": self.business_date.isoformat(), "shift": shift_label},
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f'data-airtime-previous-balance="{expected}"')
            balance_input = re.search(r'<input[^>]*name="airtime_balance_1"[^>]*>', response.content.decode()).group()
            self.assertIn(f'value="{displayed or expected}"', balance_input)

        save_record(previous_date, "Day", {"airtime_balance_1": "11.00", "airtime_balance": "11.00"})
        self.site.shift_count = 1
        self.site.save(update_fields=["shift_count"])
        assert_starting_balance("Day", "11.00")

        CashUpShiftRecord.objects.filter(site=self.site).delete()
        self.site.shift_count = 2
        self.site.save(update_fields=["shift_count"])
        save_record(previous_date, "Night", {"airtime_balance": "90.00"})
        save_record(self.business_date, "Day", {
            "airtime_balance_1": "20.00",
            "airtime_balance_2": "25.00",
            "airtime_balance": "25.00",
            "airtime_slip_1": "5.00",
        })
        save_record(self.business_date, "Night", {"airtime_balance_1": "5670.00", "airtime_balance": "5670.00"})
        assert_starting_balance("Night", "25.00")
        assert_starting_balance("Day", "90.00", "85.00")

        CashUpShiftRecord.objects.filter(site=self.site).delete()
        self.site.shift_count = 3
        self.site.save(update_fields=["shift_count"])
        save_record(previous_date, "Evening", {"airtime_balance": "90.00"})
        save_record(self.business_date, "Morning", {"airtime_balance": "30.00"})
        save_record(self.business_date, "Afternoon", {
            "airtime_balance_1": "31.00",
            "airtime_balance_2": "32.00",
            "airtime_balance": "32.00",
            "airtime_slip_1": "1.00",
        })
        assert_starting_balance("Evening", "32.00")
        assert_starting_balance("Afternoon", "30.00", "29.00")
        assert_starting_balance("Morning", "90.00")

    def test_airtime_multiple_entries_save_and_reload_with_individual_differences(self):
        response = self.client.post(
            f"{self.cash_up_url('airtime')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "airtime_count": "2",
                "airtime_shift_nr": "SHIFT-1",
                "airtime_shift_nr_2": "SHIFT-2",
                "airtime_balance_1": "50.00",
                "airtime_slip_1": "12.00",
                "airtime_system_1": "10.00",
                "airtime_loaded_1": "20.00",
                "airtime_slip_2": "3.00",
                "airtime_system_2": "4.00",
                "airtime_loaded_2": "5.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        record = CashUpShiftRecord.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(record.values["airtime_shift_nr"], "SHIFT-1")
        self.assertEqual(record.values["airtime_shift_nr_2"], "SHIFT-2")
        self.assertEqual(record.values["airtime_slip_1"], "12.00")
        self.assertEqual(record.values["airtime_slip_2"], "3.00")
        self.assertEqual(record.values["airtime_balance_1"], "50.00")
        self.assertEqual(record.values["airtime_balance_2"], "52.00")
        self.assertEqual(record.values["airtime_balance"], "52.00")

        response = self.client.get(self.cash_up_url("airtime"), self.cash_up_query())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="airtime_shift_nr"')
        self.assertContains(response, 'name="airtime_shift_nr_2"')
        self.assertContains(response, 'name="airtime_slip_2"')
        self.assertEqual(response.content.decode().count("data-airtime-difference"), 2)
        self.assertContains(response, "2.00")
        self.assertContains(response, "-1.00")

        response = self.client.post(
            f"{self.cash_up_url('airtime')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "airtime_count": "2",
                "airtime_shift_nr": "SHIFT-1",
                "airtime_shift_nr_2": "SHIFT-2",
                "airtime_balance_1": "50.00",
                "airtime_slip_1": "12.00",
                "airtime_system_1": "10.00",
                "airtime_loaded_1": "20.00",
                "airtime_slip_2": "3.00",
                "airtime_system_2": "4.00",
                "airtime_loaded_2": "5.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.values["airtime_balance"], "52.00")

    def test_save_and_continue_saves_current_tab_without_switching_tabs(self):
        response = self.client.post(
            f"{self.cash_up_url('cash')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "save_mode": "continue",
                "coins": "9.00",
                "safedrops": "4.00",
            },
        )
        self.assertRedirects(
            response,
            f"{self.cash_up_url('cash')}?date={self.business_date.isoformat()}&shift=Night&saved=cash&workflow_saved=cash",
            fetch_redirect_response=False,
        )
        record = CashUpShiftRecord.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(record.values["coins"], "9.00")
        self.assertEqual(record.values["totals"]["cash"], "13.00")

    def test_save_and_exit_saves_drafts_from_all_tabs(self):
        all_tab_values = {
            "cash": {"coins": "15.00", "safedrops": "6.00"},
            "vouchers": {"voucher_count": "2", "voucher_1": "3.00", "voucher_2": "2.00"},
            "airtime": {"airtime_shift_nr": "SHIFT12345", "airtime_loaded": "11.00"},
        }
        response = self.client.post(
            f"{self.cash_up_url('airtime')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "save_mode": "exit",
                "airtime_shift_nr": "SHIFT12345",
                "all_tab_values": json.dumps(all_tab_values),
            },
        )
        self.assertRedirects(
            response,
            f"{reverse('site-dashboard', args=[self.site.slug])}?date={self.business_date.isoformat()}&shift=Night&cashup_saved=all&workflow_saved=airtime",
            fetch_redirect_response=False,
        )
        record = CashUpShiftRecord.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(record.values["coins"], "15.00")
        self.assertEqual(record.values["safedrops"], "6.00")
        self.assertEqual(record.values["voucher_2"], "2.00")
        self.assertEqual(record.values["airtime_loaded"], "11.00")
        self.assertEqual(record.values["totals"]["cash"], "21.00")
        self.assertEqual(record.values["totals"]["vouchers"], "5.00")

    def test_configured_machine_and_additional_fields_are_dynamic_and_preserved(self):
        eft_point = SpeedPoint.objects.create(site=self.site, point_id="EFT-3", serial_number="DRIVE-3")
        manual_point = ManualShopPoint.objects.create(site=self.site, point_id="SHOP-2", serial_number="MANUAL-2")
        additional = CashUpAdditional.objects.create(site=self.site, additional_field="Till variance")
        for section, label in (("eft-driveway", "EFT/Driveway DRIVE-3"), ("manual-shop", "Manual/Shop MANUAL-2"), ("cash-up-additional", "Till variance")):
            page = self.client.get(self.cash_up_url(section), self.cash_up_query())
            self.assertContains(page, label)
            self.assertContains(page, 'class="cash-up-fields cash-up-fields--stacked"')
            self.assertContains(page, 'data-cash-up-total="')
        eft_page = self.client.get(self.cash_up_url("eft-driveway"), self.cash_up_query())
        self.assertNotContains(eft_page, "EFT-3")
        self.assertContains(eft_page, 'class="currency-prefix">ZAR</span>')
        self.assertEqual(self.client.post(
            f"{self.cash_up_url('eft-driveway')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                f"eft_{eft_point.pk}": "100.00",
            },
        ).status_code, 302)
        self.assertEqual(self.client.post(
            f"{self.cash_up_url('manual-shop')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                f"manual_{manual_point.pk}": "55.25",
            },
        ).status_code, 302)
        self.assertEqual(self.client.post(
            f"{self.cash_up_url('cash-up-additional')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                f"additional_{additional.pk}": "3.75",
            },
        ).status_code, 302)
        record = CashUpShiftRecord.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(record.values[f"eft_{eft_point.pk}"], "100.00")
        self.assertEqual(record.values[f"manual_{manual_point.pk}"], "55.25")
        self.assertEqual(record.values[f"additional_{additional.pk}"], "3.75")

    def test_sales_account_total_and_airtime_difference_start_from_saved_values(self):
        self.client.post(
            f"{self.cash_up_url('sales-account')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "sales_account_count": "2",
                "sales_account_1": "20.00",
                "sales_account_2": "5.00",
                "arrears_account_count": "2",
                "arrears_account_1": "7.50",
                "arrears_account_2": "2.50",
            },
        )
        sales_page = self.client.get(self.cash_up_url("sales-account"), self.cash_up_query())
        self.assertContains(sales_page, "35.00")
        self.assertContains(sales_page, "25.00")
        self.assertContains(sales_page, "10.00")
        self.assertContains(sales_page, 'class="cash-up-fields cash-up-fields--stacked"')
        self.client.post(
            f"{self.cash_up_url('airtime')}?date={self.business_date.isoformat()}&shift=Night",
            {
                "business_date": self.business_date.isoformat(),
                "shift_label": "Night",
                "airtime_slip": "20.00",
                "airtime_system": "13.75",
            },
        )
        airtime_page = self.client.get(self.cash_up_url("airtime"), self.cash_up_query())
        self.assertContains(airtime_page, "6.25")
