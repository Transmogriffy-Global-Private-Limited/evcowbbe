"""Opt-in worker. Only committed PENDING commands are eligible to send."""
import os
from django.core.management.base import BaseCommand, CommandError
from cow.models import HALCommand
from cow.hal.client import send_operation_once

class Command(BaseCommand):
    help="Send a bounded batch of pending HAL commands, disabled by default"
    def add_arguments(self,p):
        p.add_argument("--limit",type=int,default=10)
    def handle(self,*args,**o):
        if os.environ.get("COW_HAL_WORKER_ENABLED","").lower() != "true":
            raise CommandError("HAL worker disabled; never dispatch physical commands implicitly")
        for id_ in HALCommand.objects.filter(state="PENDING").order_by("created_at").values_list("id",flat=True)[:max(0,min(100,o["limit"]))]:
            result=send_operation_once(id_)
            self.stdout.write(f"{id_} {result}")
