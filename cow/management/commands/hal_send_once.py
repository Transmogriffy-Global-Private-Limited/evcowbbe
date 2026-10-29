from uuid import UUID
from django.core.management.base import BaseCommand, CommandError
from cow.hal.client import send_operation_once

class Command(BaseCommand):
    help="Attempt delivery exactly once for an explicitly identified pending operation; never blindly replay"
    def add_arguments(self,p):
        p.add_argument("operation_id")
        p.add_argument("--approve",action="store_true")
    def handle(self,*args,**o):
        if not o["approve"]:
            raise CommandError("Explicit --approve required")
        try:identity=UUID(o["operation_id"])
        except ValueError:raise CommandError("Expected UUID")
        self.stdout.write(send_operation_once(identity))
