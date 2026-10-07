from io import BytesIO
import json
import re
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from html import unescape
from datetime import timedelta

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

    def test_hq_user_admin_exposes_site_form_permissions_in_grappelli_inline(self):
        admin_user = User.objects.create_superuser(username="permission-admin", password="test-pass-123", email="permission-admin@example.com")
        site = Site.objects.create(name="Permission Admin Site")
        site_user = User.objects.create_user(username="permission-site-user", password="test-pass-123")
        UserProfile.objects.create(user=site_user, role=UserProfile.Role.SITE, site=site)
        client = Client()
        client.force_login(admin_user)

        response = client.get(reverse("admin:auth_user_change", args=[site_user.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Site form permissions")
        self.assertContains(response, "selectfilter")

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
        self.assertContains(
            page,
            f"{reverse('site-form-history', args=[self.site_one.slug, 'book-in-fuel'])}?date={selected_date.isoformat()}&amp;shift=Day",
        )
        self.assertNotContains(page, other_tank.name)

        response = self.client.post(shift_url, {
            "business_date": selected_date.isoformat(),
            "shift_label": "Day",
            "delivery_count": "1",
            "delivery_1_truck_reference": "Truck 1",
            f"delivery_1_tank_{tank.pk}_qty_litres": "1250.50",
        })

        self.assertEqual(response.status_code, 302)
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

    def test_site_forms_and_user_management_link_to_history(self):
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        SiteGroup.objects.create(site=self.site_one, name="Test group", form_permissions=["book-in-fuel"])
        self.client.force_login(self.site_user)
        history_url = reverse("site-history", args=[self.site_one.slug])
        urls = (
            reverse("site-users", args=[self.site_one.slug]),
            reverse("site-user-create", args=[self.site_one.slug]),
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
        user_response = self.client.post(reverse("site-user-create", args=[self.site_one.slug]), {
            "username": "site-operator",
            "first_name": "Site",
            "last_name": "Operator",
            "email": "operator@example.com",
            "password": "safe-test-password-123",
            "confirm_password": "safe-test-password-123",
            "groups": [group.pk],
        })

        self.assertEqual(user_response.status_code, 302)
        created_user = User.objects.get(username="site-operator")
        self.assertEqual(created_user.fuel_profile.site, self.site_one)
        self.assertIn(created_user, group.users.all())
        self.assertIn(group.django_group, created_user.groups.all())
        self.assertIn(created_user, group.django_group.user_set.all())
        self.assertEqual(self.site_one.audit_events.count(), 2)
        self.assertContains(self.client.get(reverse("site-history", args=[self.site_one.slug])), "Site group")
        self.assertContains(self.client.get(reverse("site-history", args=[self.site_one.slug])), "Site user")

    def test_site_user_editor_changes_only_users_at_the_current_site(self):
        self.site_user.fuel_profile.can_manage_users = True
        self.site_user.fuel_profile.save(update_fields=["can_manage_users"])
        target = User.objects.create_user(username="site-target", password="initial-password")
        UserProfile.objects.create(user=target, role=UserProfile.Role.SITE, site=self.site_one)
        other_site_user = User.objects.create_user(username="other-site-target", password="initial-password")
        UserProfile.objects.create(user=other_site_user, role=UserProfile.Role.SITE, site=self.site_two)
        group = SiteGroup.objects.create(site=self.site_one, name="Book-in operators")
        self.client.force_login(self.site_user)
        edit_url = reverse("site-user-edit", args=[self.site_one.slug, target.pk])

        page = self.client.get(edit_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "selectfilter")
        self.assertContains(page, "Site permissions")
        self.assertEqual(self.client.get(reverse("site-user-edit", args=[self.site_one.slug, other_site_user.pk])).status_code, 404)

        response = self.client.post(edit_url, {
            "username": "site-target",
            "first_name": "Updated",
            "last_name": "Operator",
            "email": "updated@example.com",
            "is_active": "on",
            "groups": [str(group.pk)],
            "form_permissions": ["book-in-fuel"],
            "can_configure": "on",
            "password": "",
            "confirm_password": "",
        })

        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertEqual(target.first_name, "Updated")
        self.assertIn(group, target.fuel_site_groups.all())
        self.assertIn(group.django_group, target.groups.all())
        self.assertEqual(target.fuel_profile.form_permissions, ["book-in-fuel"])
        self.assertTrue(target.fuel_profile.can_configure)
        self.assertFalse(target.fuel_profile.can_manage_users)
        self.assertTrue(self.site_one.audit_events.filter(category="Site user", object_label="site-target", action="changed").exists())

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
        self.assertNotContains(add_user_page, "Superuser status")
        user_formset = add_user_page.context["inline_admin_formsets"][0].formset
        user_inline = user_formset.forms[0]
        user_data = {
            "username": "django-site-member",
            "password1": "safe-test-password-123",
            "password2": "safe-test-password-123",
            f"{user_formset.prefix}-TOTAL_FORMS": "1",
            f"{user_formset.prefix}-INITIAL_FORMS": "0",
            f"{user_formset.prefix}-MIN_NUM_FORMS": "0",
            f"{user_formset.prefix}-MAX_NUM_FORMS": "1",
            user_inline["role"].html_name: UserProfile.Role.SITE,
            user_inline["site"].html_name: str(self.site_one.pk),
            user_inline["form_permissions"].html_name: ["cash-up"],
        }
        add_user_response = self.client.post(add_user_entry["Location"], user_data)
        self.assertEqual(add_user_response.status_code, 302)
        new_site_user = User.objects.get(username="django-site-member")
        self.assertEqual(new_site_user.fuel_profile.site, self.site_one)
        self.assertEqual(new_site_user.fuel_profile.role, UserProfile.Role.SITE)
        self.assertEqual(new_site_user.fuel_profile.form_permissions, ["cash-up"])

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

    def test_reading_form_has_three_tabs_with_the_current_form_selected(self):
        response = self.client.get(
            reverse("fuel-reading-entry", args=[self.site.slug, "tank-reading"]),
            {"date": self.business_date.isoformat(), "shift": "Night"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fuel Readings")
        self.assertContains(response, 'aria-selected="true"')
        for reading_type in ("tank-reading", "mechanical-readings", "electronic-readings"):
            self.assertContains(response, reverse("fuel-reading-entry", args=[self.site.slug, reading_type]))

    def test_configured_shifts_drive_entry_and_reading_history(self):
        self.tank.capacity_litres = "25000.00"
        self.tank.save(update_fields=["capacity_litres"])
        entry_query = {"date": self.business_date.isoformat(), "shift": "Night"}
        dashboard = self.client.get(reverse("site-dashboard", args=[self.site.slug]), entry_query)
        self.assertEqual(dashboard.status_code, 200)
        for reading_type in ("tank-reading", "mechanical-readings", "electronic-readings"):
            self.assertContains(dashboard, reverse("fuel-reading-entry", args=[self.site.slug, reading_type]))
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
        self.assertEqual(self.client.post(tank_post_url, {
            "business_date": self.business_date.isoformat(),
            "shift_label": "Night",
            f"tank_{self.tank.pk}_dip": "1234.50",
            f"tank_{self.tank.pk}_depth_cm": "112.25",
            f"tank_{self.tank_two.pk}_dip": "987.65",
            f"tank_{self.tank_two.pk}_depth_cm": "88.50",
        }).status_code, 302)
        tank_reading = TankDipReading.objects.get(daily_reading__site=self.site, daily_reading__business_date=self.business_date, tank=self.tank)
        self.assertEqual(str(tank_reading.depth_cm), "112.25")
        reopened_tank_page = self.client.get(tank_url, entry_query)
        self.assertContains(reopened_tank_page, 'value="112.25"')

        mechanical_url = reverse("fuel-reading-entry", args=[self.site.slug, "mechanical-readings"])
        mechanical_post_url = f"{mechanical_url}?date={self.business_date.isoformat()}&shift=Night"
        mechanical_page = self.client.get(mechanical_url, entry_query)
        self.assertContains(mechanical_page, self.nozzle.name)
        self.assertContains(mechanical_page, self.nozzle_two.name)
        self.assertContains(mechanical_page, self.tank_two.name)
        self.assertEqual(self.client.post(mechanical_post_url, {
            "business_date": self.business_date.isoformat(),
            "shift_label": "Night",
            f"nozzle_{self.nozzle.pk}_mechanical": "234.56",
            f"nozzle_{self.nozzle_two.pk}_mechanical": "456.78",
        }).status_code, 302)

        electronic_url = reverse("fuel-reading-entry", args=[self.site.slug, "electronic-readings"])
        electronic_post_url = f"{electronic_url}?date={self.business_date.isoformat()}&shift=Night"
        electronic_page = self.client.get(electronic_url, entry_query)
        self.assertContains(electronic_page, self.nozzle.name)
        self.assertContains(electronic_page, self.nozzle_two.name)
        self.assertEqual(self.client.post(electronic_post_url, {
            "business_date": self.business_date.isoformat(),
            "shift_label": "Night",
            f"nozzle_{self.nozzle.pk}_electronic": "345.67",
            f"nozzle_{self.nozzle_two.pk}_electronic": "567.89",
        }).status_code, 302)

        reading = DailyFuelReading.objects.get(site=self.site, business_date=self.business_date, shift_label="Night")
        self.assertEqual(str(TankDipReading.objects.get(daily_reading=reading, tank=self.tank).dip_litres), "1234.50")
        self.assertEqual(str(TankDipReading.objects.get(daily_reading=reading, tank=self.tank_two).dip_litres), "987.65")
        nozzle_reading = NozzleFuelReading.objects.get(daily_reading=reading, nozzle=self.nozzle)
        self.assertEqual(str(nozzle_reading.mechanical_reading), "234.56")
        self.assertEqual(str(nozzle_reading.electronic_reading), "345.67")
        self.assertEqual(self.site.audit_events.filter(category="Tank Reading").count(), 1)
        self.assertEqual(self.site.audit_events.filter(category="Mechanical Readings").count(), 1)
        self.assertEqual(self.site.audit_events.filter(category="Electronic Readings").count(), 1)
        for reading_type, label in (("tank-reading", "Tank Reading"), ("mechanical-readings", "Mechanical Readings"), ("electronic-readings", "Electronic Readings")):
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

    def test_cash_up_tabs_are_shown_on_dashboard_and_entry_page(self):
        dashboard = self.client.get(
            reverse("site-dashboard", args=[self.site.slug]),
            self.cash_up_query(),
        )
        self.assertEqual(dashboard.status_code, 200)
        cash_page = self.client.get(self.cash_up_url("cash"), self.cash_up_query())
        self.assertEqual(cash_page.status_code, 200)
        for section, label in (
            ("cash", "Cash"),
            ("vouchers", "Vouchers"),
            ("airtime", "Airtime"),
            ("sales-account", "Sales Account"),
            ("payouts", "Payouts"),
            ("eft-driveway", "EFT/Driveway"),
            ("manual-shop", "Manual/Shop"),
            ("cash-up-additional", "Additional"),
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
        self.assertEqual(cash_page.content.decode().count('value="tab"'), 1)
        cash_page_html = cash_page.content.decode()
        self.assertLess(cash_page_html.index("cash-up-actions--top"), cash_page_html.index('aria-label="Cash up sections"'))
        self.assertContains(cash_page, "cash-up-page-heading")
        self.assertContains(cash_page, "cash-up-form")

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
        self.assertEqual(response.content.decode().count('class="currency-prefix">ZAR</span>'), 6)
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
        self.assertContains(response, "Total Airtime")
        self.assertNotContains(response, "Total Airtime Loaded")

    def test_save_and_continue_saves_current_tab_and_opens_next_tab(self):
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
            f"{self.cash_up_url('vouchers')}?date={self.business_date.isoformat()}&shift=Night&saved=cash",
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
            f"{reverse('site-dashboard', args=[self.site.slug])}?date={self.business_date.isoformat()}&shift=Night&cashup_saved=all",
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
