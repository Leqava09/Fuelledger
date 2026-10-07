from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0017_tank_capacity_litres_tankdipreading_depth_cm'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CashUpShiftRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('business_date', models.DateField()),
                ('shift_label', models.CharField(max_length=60)),
                ('values', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_up_records_created', to=settings.AUTH_USER_MODEL)),
                ('site', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_up_records', to='ledger.site')),
            ],
            options={
                'ordering': ['-business_date', 'shift_label'],
            },
        ),
        migrations.AddConstraint(
            model_name='cashupshiftrecord',
            constraint=models.UniqueConstraint(fields=('site', 'business_date', 'shift_label'), name='unique_cashup_record_per_site_shift'),
        ),
    ]