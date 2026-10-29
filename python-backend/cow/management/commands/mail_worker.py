from django.core.management.base import BaseCommand
from cow.services.mail import claim_next, deliver_one

class Command(BaseCommand):
    help="Process a bounded batch of encrypted credential outbox deliveries"
    def add_arguments(self,p):
        p.add_argument("--limit",type=int,default=20)
    def handle(self,*args,**opts):
        for _ in range(max(0,min(100,opts["limit"]))):
            id_=claim_next()
            if not id_:break
            delivered=deliver_one(id_)
            self.stdout.write("delivered" if delivered else "deferred")
