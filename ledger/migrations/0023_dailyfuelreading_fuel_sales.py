from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0022_userprofile_phone_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="dailyfuelreading",
            name="fuel_sales",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
