import re
from html import unescape
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from ledger.calculations import FormulaError, calculate_values, validate_formula
from ledger.admin import NozzleInlineForm, SiteAdminForm, SpeedPointInline, SpeedPointInlineForm, TankInlineForm
from ledger.models import CashUpField, FuelBooking, Site, SpeedPoint, Tank, UserProfile


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
    def test_grappelli_site_form_contains_contact_fields_and_nested_tank_nozzle_inlines(self):
        admin_user = User.objects.create_superuser(username="admin", password="test-pass-123", email="admin@example.com")
        client = Client()
        client.force_login(admin_user)

        response = client.get(reverse("admin:ledger_site_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Address")
        self.assertContains(response, "Contact email")
        self.assertContains(response, "Contact Number")
        self.assertContains(response, "Manager name")
        self.assertContains(response, "Site Name")
        self.assertContains(response, "Amount of shifts")
        self.assertContains(response, "Tanks")
        self.assertContains(response, "Nozzles")
        self.assertContains(response, "Speed points")
        self.assertContains(response, "Speed point no/ID")
        self.assertContains(response, "Speed point Serial No")
        self.assertContains(response, "nested_admin")
        self.assertContains(response, "site_shift_selector.js")
        self.assertContains(response, "fuel-tanks-inline")
        self.assertContains(response, "fuel-speed-points-inline")
        html = response.content.decode()
        self.assertNotIn('id="id_slug"', html)
        field_positions = [html.index(f'id="id_{field}"') for field in ("manager_name", "contact_number", "contact_email", "address")]
        self.assertEqual(field_positions, sorted(field_positions))
        for field_name in ("name", "manager_name", "contact_number", "contact_email"):
            self.assertIn("width: 50%", html[html.index(f'id="id_{field_name}"') - 300:html.index(f'id="id_{field_name}"') + 300])

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
        self.assertEqual(SpeedPointInline.extra, 0)
        self.assertEqual(SpeedPointInline.fields, ("point_id", "serial_number"))

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
        self.assertTrue(all(
            SiteAdminForm().fields[field_name].widget.attrs["style"].startswith("width: 50%;")
            for field_name in ("name", "manager_name", "contact_number", "contact_email")
        ))

    def test_tank_heading_does_not_repeat_tank_prefix(self):
        admin_user = User.objects.create_superuser(username="tank-admin", password="test-pass-123", email="tank-admin@example.com")
        site = Site.objects.create(name="Tank Heading Test")
        Tank.objects.create(site=site, name="Tank 1", product="Diesel 50 ppm")
        client = Client()
        client.force_login(admin_user)

        response = client.get(reverse("admin:ledger_site_change", args=[site.pk]))

        self.assertEqual(response.status_code, 200)
        page_text = " ".join(re.sub(r"<[^>]*>", " ", unescape(response.content.decode())).split())
        tank_heading_fragments = re.findall(r".{0,30}Diesel 50 ppm.{0,10}", page_text)
        self.assertTrue("Tank 1 - Diesel 50 ppm" in page_text, repr(tank_heading_fragments))
        self.assertIn('id="id_tanks-0-product"', response.content.decode())


class SiteAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.site_one = Site.objects.create(name="Puma Central")
        self.site_two = Site.objects.create(name="Texan North")
        self.site_user = User.objects.create_user(username="cashier", password="test-pass-123")
        UserProfile.objects.create(user=self.site_user, role=UserProfile.Role.SITE, site=self.site_one)
        self.hq_user = User.objects.create_user(username="hq", password="test-pass-123")
        UserProfile.objects.create(user=self.hq_user, role=UserProfile.Role.HQ)

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

        self.assertEqual(self.client.get(reverse("hq-dashboard")).status_code, 200)
        response = self.client.get(reverse("site-dashboard", args=[self.site_two.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("hq-dashboard"))

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
        for key in ("book-in-fuel", "daily-fuel-readings", "cash-up"):
            self.assertContains(past_response, f"/sites/{self.site_one.slug}/forms/{key}/?date={selected_date.isoformat()}")

    def test_future_calendar_date_defaults_back_to_today(self):
        self.client.force_login(self.site_user)
        today = timezone.localdate()
        future_date = today + timedelta(days=1)

        response = self.client.get(reverse("site-dashboard", args=[self.site_one.slug]), {"date": future_date.isoformat()})

        self.assertEqual(response.context["selected_date"], today)

    def test_blank_form_page_preserves_date_and_is_site_restricted(self):
        self.client.force_login(self.site_user)
        selected_date = timezone.localdate() - timedelta(days=2)

        response = self.client.get(reverse("blank-site-form", args=[self.site_one.slug, "book-in-fuel"]), {
            "date": selected_date.isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Book IN Fuel")
        self.assertContains(response, selected_date.strftime("%d %b %Y"))
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

        page = self.client.get(url, {"date": selected_date.isoformat()})

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Tank 1")
        self.assertContains(page, "Diesel")
        self.assertContains(page, "Qty L booked in")
        self.assertNotContains(page, other_tank.name)

        response = self.client.post(url, {
            "business_date": selected_date.isoformat(),
            f"tank_{tank.pk}_qty_litres": "1250.50",
        })

        self.assertEqual(response.status_code, 302)
        booking = FuelBooking.objects.get(site=self.site_one, tank=tank, business_date=selected_date)
        self.assertEqual(str(booking.qty_litres), "1250.50")
        reopened = self.client.get(url, {"date": selected_date.isoformat()})
        self.assertContains(reopened, 'value="1250.50"')
