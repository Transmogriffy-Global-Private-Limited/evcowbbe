"""Booking-level final charge worker; dry-run by default, no HAL IO."""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from cow.models import CoWBookingCommercialCommitment
from cow.services.booking_providers import trusted_provider
from cow.services.booking_commercial import settle_completed_booking


class Command(BaseCommand):
    help = 'Inspect ready CoW completed bookings; --execute posts only under explicit release gates'

    def add_arguments(self, parser):
        parser.add_argument('--limit',type=int,default=25)
        parser.add_argument('--execute',action='store_true')

    def handle(self,*args,**options):
        limit=options['limit']
        if not 1<=limit<=100:
            raise CommandError('limit must be 1..100')
        bookings=list(CoWBookingCommercialCommitment.objects.filter(
            job__state='COMPLETED',state__in=('FUNDED','START_PENDING','IN_SERVICE','CLOSING'))
            .order_by('created_at','pk').values_list('pk',flat=True)[:limit])
        if not options['execute']:
            self.stdout.write(f'COW_BOOKING_SETTLEMENT_DRY_RUN=PASS; CANDIDATES={len(bookings)}; POSTED=0')
            for key in bookings:
                self.stdout.write(f'REQUIRES_VERIFIED_PROOF={key}')
            return
        if not (getattr(settings,'COW_BOOKING_COMMERCIAL_SETTLEMENT_ENABLED',False) and
                getattr(settings,'COW_HAL_V1_FINANCIAL_POSTING_ENABLED',False)):
            raise CommandError('financial posting release gates disabled')
        provider=trusted_provider()
        resolve=getattr(provider,'resolve_completion_ref',None)
        if not callable(resolve):
            raise CommandError('trust provider must resolve independently recorded driver completion receipt')
        success=0
        for key in bookings:
            ref=resolve(key)
            result=settle_completed_booking(
                booking_id=key,completion_ref=ref,
                verify_driver_completion=provider.verify_driver_completion)
            self.stdout.write(f'BOOKING={key}; STATE={result["state"]}')
            success+=result['state']=='POSTED'
        self.stdout.write(f'COW_BOOKING_SETTLEMENT_WORKER=FINISHED; POSTED={success}; CHECKED={len(bookings)}')
