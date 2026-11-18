"""Command reconciliation worker: never remote command resubmission."""
from django.core.management.base import BaseCommand, CommandError
from cow.hal import v1_charging
from cow.models import HALV1ChargingCommand


class Command(BaseCommand):
    help = 'Inspect previously attempted uncertain CMS-to-Go charging commands'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        limit=options['limit']
        if type(limit) is not int or not 1<=limit<=100:
            raise CommandError('limit must be 1..100')
        ids=list(HALV1ChargingCommand.objects.filter(
            attempted_at__isnull=False,
            state__in=('DELIVERY_ATTEMPTED','ACK_RECORDED','PROTOCOL_ACKNOWLEDGED',
                       'RECONCILIATION_REQUIRED')).order_by('created_at','id')
            .values_list('id',flat=True)[:limit])
        if not options['execute']:
            self.stdout.write('HAL_V1_COMMAND_RECONCILE_DRY_RUN; COUNT='+str(len(ids)))
            return
        for command_id in ids:
            result=v1_charging.reconcile_command(command_id)
            self.stdout.write(str(command_id)+' '+result['state'])
