from django.db import migrations, models


def assign_first_configured_shift(apps, schema_editor):
	FuelBooking = apps.get_model("ledger", "FuelBooking")
	Site = apps.get_model("ledger", "Site")
	database = schema_editor.connection.alias
	shift_by_site = {
		site.pk: "Morning" if site.shift_count == 3 else "Day"
		for site in Site.objects.using(database).only("pk", "shift_count")
	}
	for booking in FuelBooking.objects.using(database).all().iterator():
		FuelBooking.objects.using(database).filter(pk=booking.pk).update(
			shift_label=shift_by_site.get(booking.site_id, "Day")
		)


class Migration(migrations.Migration):
	dependencies = [
		("ledger", "0008_userprofile_can_manage_users_dailyfuelreading_and_more"),
	]

	operations = [
		migrations.RemoveConstraint(
			model_name="fuelbooking",
			name="unique_fuel_booking_per_site_tank_date",
		),
		migrations.AddField(
			model_name="fuelbooking",
			name="shift_label",
			field=models.CharField(max_length=30, null=True),
		),
		migrations.RunPython(assign_first_configured_shift, migrations.RunPython.noop),
		migrations.AlterField(
			model_name="fuelbooking",
			name="shift_label",
			field=models.CharField(max_length=30),
		),
		migrations.AddConstraint(
			model_name="fuelbooking",
			constraint=models.UniqueConstraint(
				fields=("site", "tank", "business_date", "shift_label"),
				name="unique_fuel_booking_per_site_tank_date_shift",
			),
		),
	]