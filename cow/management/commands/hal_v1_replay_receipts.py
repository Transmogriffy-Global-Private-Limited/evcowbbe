"""Explicit local immutable fact-receipt reconciliation. No HAL network I/O."""
from django.core.management.base import BaseCommand, CommandError
from cow.hal import v1_charging


class Command(BaseCommand):
    help='Replay pending CoW v1 fact receipts without resending any HAL command'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        limit=options['limit']
        if not 1<=limit<=100:raise CommandError('limit outside 1..100')
        if not options['execute']:
            self.stdout.write('HAL_V1_RECEIPT_REPLAY_DRY_RUN; NO_DB_MUTATION')
            return
        count=v1_charging.replay_pending(limit=limit)
        self.stdout.write('HAL_V1_RECEIPTS_PROCESSED='+str(count))
