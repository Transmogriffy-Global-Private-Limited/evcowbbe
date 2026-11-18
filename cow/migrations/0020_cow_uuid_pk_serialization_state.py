"""Align six newly introduced UUID primary-key migration states with Django.

The published migration 0019 remains immutable. This appends an explicit
field-state correction; database DDL must be checked separately in an isolated
PostgreSQL harness before any migration is applied.
"""
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('cow', '0019_cow_wallet_location_operational')]

    operations = [
        migrations.AlterField(
            model_name='cowbookingpreviewticket',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='cowcompletionreview',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='cowhandoverticket',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='cowlocationobservation',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='cowwalletoperation',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='cowwalletrefund',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
    ]
