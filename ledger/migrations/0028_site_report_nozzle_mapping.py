from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("ledger", "0027_alter_manualshoppoint_point_id_and_more"),
	]

	operations = [
		migrations.AddField(
			model_name="site",
			name="report_nozzle_mapping",
			field=models.JSONField(blank=True, default=dict),
		),
	]