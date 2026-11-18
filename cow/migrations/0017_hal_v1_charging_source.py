"""HAL v1 source-only charging projection. Never apply outside an approved migration gate.

Specified against canonical cow/models.py after M1 (0016). Not executed yet.
"""
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('cow', '0016_hal_v1_machine')]
    operations = [
        migrations.CreateModel(
            name='HALV1ChargingPermit',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('scope_id', models.UUIDField()),
                ('channel', models.CharField(max_length=12, choices=[('ROAMING','ROAMING'),('FIXED_QR','FIXED_QR')])),
                ('wallet_hold_reference', models.CharField(max_length=160, unique=True)),
                ('commercial_snapshot', models.JSONField()),
                ('location_evidence', models.JSONField()),
                ('operational_evidence', models.JSONField()),
                ('limit_type', models.CharField(max_length=8)),
                ('energy_limit_wh', models.BigIntegerField(default=0)),
                ('energy_limit_source', models.CharField(max_length=24, default='NONE')),
                ('max_duration_seconds', models.BigIntegerField(default=0)),
                ('duration_limit_source', models.CharField(max_length=24, default='NONE')),
                ('expires_at', models.DateTimeField()),
                ('state', models.CharField(max_length=32, default='RESERVED')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='cow.principal')),
                ('job', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='cow.servicejob')),
                ('charger', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='cow.charger')),
                ('connector', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='cow.chargerconnector')),
            ],
            options={
                'indexes':[models.Index(fields=['scope_id','state'],name='cow_v1_permit_scope_idx')],
                'constraints':[models.CheckConstraint(condition=models.Q(state__in=['RESERVED','BOUND','SETTLEMENT_PENDING','RELEASED','SETTLED','RECONCILIATION_REQUIRED']),name='cow_v1_permit_state_valid')],
            },
        ),
        migrations.CreateModel(
            name='HALV1ChargingIntent',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('idempotency_key', models.CharField(max_length=128)),
                ('state', models.CharField(max_length=32, default='PERSISTED')),
                ('credential_ciphertext', models.TextField()),
                ('credential_sha256', models.CharField(max_length=64)),
                ('credential_expires_at', models.DateTimeField()),
                ('command_expires_at', models.DateTimeField()),
                ('mapping_snapshot', models.JSONField()),
                ('permit', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='cow.halv1chargingpermit')),
            ],
            options={'constraints':[models.CheckConstraint(condition=models.Q(state__in=[
                'PERSISTED','DELIVERY_ATTEMPTED','ACCEPTED_FOR_DELIVERY','PROTOCOL_ACKNOWLEDGED',
                'ACTUALLY_STARTED','REJECTED','EXPIRED','RECONCILIATION_REQUIRED']),name='cow_v1_intent_state_valid')]},
        ),
        migrations.CreateModel(
            name='HALV1ChargingSession',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('hal_transaction_id', models.UUIDField(unique=True)),
                ('ocpp_transaction_id', models.BigIntegerField()),
                ('state', models.CharField(max_length=32, default='ACTIVE')),
                ('started_at', models.DateTimeField()),
                ('last_meter_wh', models.BigIntegerField()),
                ('meter_start_wh', models.BigIntegerField()),
                ('meter_end_wh', models.BigIntegerField(blank=True, null=True)),
                ('meter_sequence', models.BigIntegerField(default=0)),
                ('meter_observed_at', models.DateTimeField(blank=True, null=True)),
                ('latest_soc_percent', models.CharField(max_length=8, blank=True, default='')),
                ('soc_sequence', models.BigIntegerField(default=0)),
                ('soc_observed_at', models.DateTimeField(blank=True, null=True)),
                ('stopped_at', models.DateTimeField(blank=True, null=True)),
                ('stop_reason', models.CharField(max_length=100, blank=True, default='')),
                ('completion_fact_id', models.UUIDField(blank=True, null=True, unique=True)),
                ('intent', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='cow.halv1chargingintent')),
                ('charger', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='cow.charger')),
                ('connector', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='cow.chargerconnector')),
            ],
            options={'constraints':[models.CheckConstraint(condition=models.Q(state__in=[
                'ACTIVE','PHYSICALLY_COMPLETED','RECONCILIATION_REQUIRED','SETTLED']),name='cow_v1_session_state_valid'),
                models.UniqueConstraint(fields=['connector'],condition=models.Q(state__in=['ACTIVE','RECONCILIATION_REQUIRED']),name='cow_v1_connector_live_guard')]},
        ),
        migrations.CreateModel(
            name='HALV1ChargingCommand',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('kind', models.CharField(max_length=5, choices=[('START','START'),('STOP','STOP')])),
                ('idempotency_key', models.CharField(max_length=128)),
                ('state', models.CharField(max_length=32, default='PERSISTED')),
                ('snapshot', models.JSONField()),
                ('trace_id', models.UUIDField(default=uuid.uuid4)),
                ('correlation_id', models.UUIDField(default=uuid.uuid4)),
                ('hal_command_id', models.UUIDField(blank=True, null=True)),
                ('provider_state', models.CharField(max_length=32, blank=True, default='')),
                ('attempted_at', models.DateTimeField(blank=True, null=True)),
                ('outcome_at', models.DateTimeField(blank=True, null=True)),
                ('failure_code', models.CharField(max_length=64, blank=True, default='')),
                ('intent', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='cow.halv1chargingintent')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='cow.halv1chargingsession', blank=True, null=True)),
            ],
            options={
                'indexes':[models.Index(fields=['state','created_at'], name='cow_v1_charge_cmd_queue_idx')],
                'constraints':[
                    models.UniqueConstraint(fields=['intent','kind'],name='cow_v1_intent_cmd_kind_unique'),
                    models.CheckConstraint(condition=models.Q(state__in=['PERSISTED','DELIVERY_ATTEMPTED','ACK_RECORDED',
                        'PROTOCOL_ACKNOWLEDGED','MATERIALIZED','REJECTED','EXPIRED','RECONCILIATION_REQUIRED']),name='cow_v1_charge_cmd_state_valid'),
                ],
            },
        ),
    ]
