from getpass import getpass
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.conf import settings
from django.contrib.auth.hashers import make_password
from cow.models import Account,Principal,AdminRole,AuditEvent
from cow.services.auth import normalize_email,validate_password

class Command(BaseCommand):
    help="Explicit first-admin bootstrap; requires COW_BOOTSTRAP_ALLOW_ADMIN=true"
    def add_arguments(self,p):
        p.add_argument("--email",required=True)
    def handle(self,*args,**opts):
        import os
        if os.environ.get("COW_BOOTSTRAP_ALLOW_ADMIN","").lower()!="true":
            raise CommandError("Admin bootstrap disabled")
        if Principal.objects.filter(plane="admin").exists():
            raise CommandError("Admin already exists; use an authorized RBAC process")
        password=getpass("Admin password (hidden): ")
        validate_password(password)
        with transaction.atomic():
            account=Account.objects.create(email=normalize_email(opts["email"]),password_hash=make_password(password),email_verified=True)
            principal=Principal.objects.create(account=account,plane="admin",status="ACTIVE")
            AdminRole.objects.create(principal=principal,role="FULL_ADMIN")
            AuditEvent.objects.create(actor=principal,target_type="Principal",target_id=principal.id,action="INITIAL_BOOTSTRAP")
        self.stdout.write("Initial admin created. Disable COW_BOOTSTRAP_ALLOW_ADMIN again.")
