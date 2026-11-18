"""Additive schema after unapplied source-only HAL charging migration 0017.

This file is intentionally not executed by the source-only installer.
"""
import uuid
import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [('cow', '0017_hal_v1_charging_source')]
    operations = [
        migrations.CreateModel(name='CoWMinimumChargingSchedule', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('inside_slabs', models.JSONField()),
            ('outside_slabs', models.JSONField(null=True, blank=True)),
            ('schedule_sha256', models.CharField(max_length=64)),
            ('authorization_ref', models.CharField(max_length=200)),
            ('authorized_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='cow.principal')),
            ('policy', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name='cow_minimum_charging',to='cow.pricingpolicy')),
        ], options={'constraints': [models.CheckConstraint(condition=~Q(schedule_sha256=''),name='cow_min_schedule_digest_present')]}),
        migrations.CreateModel(name='CoWBookingCommercialCommitment', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('booking_key', models.CharField(max_length=128)),
            ('request_sha256', models.CharField(max_length=64)),
            ('snapshot', models.JSONField()),
            ('snapshot_sha256', models.CharField(max_length=64)),
            ('fixed_fees_paise', models.PositiveBigIntegerField()),
            ('minimum_charging_paise', models.PositiveBigIntegerField()),
            ('protected_buffer_paise', models.PositiveBigIntegerField()),
            ('reserved_paise', models.PositiveBigIntegerField()),
            ('approved_distance_m', models.PositiveBigIntegerField()),
            ('currency', models.CharField(max_length=3,default='INR')),
            ('state', models.CharField(max_length=28,default='FUNDED')),
            ('job', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name='commercial_commitment',to='cow.servicejob')),
            ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='cow.principal')),
            ('wallet', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='cow_booking_commitments',to='cow.walletaccount')),
        ], options={'constraints': [
            models.UniqueConstraint(fields=['owner','booking_key'],name='cow_book_key_per_owner'),
            models.CheckConstraint(condition=Q(state__in=['FUNDED','START_PENDING','IN_SERVICE','CLOSING','RECONCILIATION_REQUIRED','SETTLED','CANCELLED']),name='cow_book_commercial_state'),
            models.CheckConstraint(condition=Q(reserved_paise__gte=0),name='cow_book_reserve_nonnegative')],
            'indexes':[models.Index(fields=['wallet','state'],name='cow_book_wallet_state_idx')]}),
        migrations.CreateModel(name='CoWBookingChargingAllocation', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('start_key', models.CharField(max_length=128)),
            ('start_sha256', models.CharField(max_length=64)),
            ('authorized_prior_wh', models.PositiveBigIntegerField()),
            ('authorized_session_wh', models.PositiveBigIntegerField()),
            ('authorized_total_wh', models.PositiveBigIntegerField()),
            ('budget_gross_paise', models.PositiveBigIntegerField()),
            ('proof_sha256', models.CharField(max_length=64)),
            ('state', models.CharField(max_length=22,default='PREPARED')),
            ('booking', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='allocations',to='cow.cowbookingcommercialcommitment')),
            ('permit', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name='cow_booking_allocation',to='cow.halv1chargingpermit')),
        ], options={'constraints':[
            models.UniqueConstraint(fields=['booking','start_key'],name='cow_book_start_key_once'),
            models.CheckConstraint(condition=Q(authorized_session_wh__gt=0),name='cow_book_session_positive'),
            models.CheckConstraint(condition=Q(state__in=['PREPARED','STARTED','COMPLETED','CANCELLED_UNSENT','RECONCILIATION_REQUIRED']),name='cow_book_allocation_state')]}),
        migrations.CreateModel(name='CoWBookingFinancialSettlement', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('final_gross_paise', models.PositiveBigIntegerField()),
            ('fixed_fees_paise', models.PositiveBigIntegerField()),
            ('charging_gross_paise', models.PositiveBigIntegerField()),
            ('minimum_adjustment_paise', models.PositiveBigIntegerField()),
            ('delivered_wh', models.PositiveBigIntegerField()),
            ('final_snapshot', models.JSONField()),
            ('final_sha256', models.CharField(max_length=64)),
            ('state', models.CharField(max_length=30,default='POSTED')),
            ('reason', models.CharField(max_length=100,blank=True,default='')),
            ('booking', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name='financial_settlement',to='cow.cowbookingcommercialcommitment')),
            ('wallet_transaction', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,null=True,blank=True,related_name='cow_booking_settlement',to='cow.wallettransaction')),
        ], options={'constraints':[models.CheckConstraint(condition=Q(state__in=['POSTED','RECONCILIATION_REQUIRED']),name='cow_book_settlement_state')]}),
    ]
