"""Explicit source-only Go v1 dispatch command. Dry-run is the default."""
from django.core.management.base import BaseCommand, CommandError
from cow.models import HALV1ChargingCommand
from cow.hal import v1_charging


class Command(BaseCommand):
    help='Dispatch only never-attempted durable CoW HAL v1 charging commands'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        limit=options['limit']
        if not 1<=limit<=100:raise CommandError('limit outside 1..100')
        ids=list(HALV1ChargingCommand.objects.filter(state='PERSISTED')
                 .order_by('created_at','id').values_list('id',flat=True)[:limit])
        if not options['execute']:
            self.stdout.write('HAL_V1_DISPATCH_DRY_RUN; COUNT='+str(len(ids)))
            return
        for command_id in ids:
            # Source operation is one-shot; do not retry after unknown outcomes.
            result=v1_charging.send_once(command_id)
            self.stdout.write(str(command_id)+' '+result['state'])
