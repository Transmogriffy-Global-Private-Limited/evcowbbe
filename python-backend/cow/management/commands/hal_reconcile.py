"""Read-only exact operation query. Never sends/replays an OCPP command."""
import time
from uuid import UUID
import httpx
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from cow.hal.contract import sign
from cow.models import HALCommand

class Command(BaseCommand):
    help="Query HAL authoritative state for an exact CMS operation. No replay."
    def add_arguments(self,p):p.add_argument("operation_id")
    def handle(self,*args,**o):
        try:id_=UUID(o["operation_id"])
        except ValueError:raise CommandError("Expected UUID")
        c=HALCommand.objects.get(id=id_)
        if not settings.COW_HAL_BASE_URL or not settings.COW_HAL_SHARED_SECRET:
            raise CommandError("HAL not connected; cannot reconcile")
        ts=int(time.time())
        path=f"/v1/operations/{id_}"
        signature=sign(settings.COW_HAL_SHARED_SECRET,ts,b"")
        with httpx.Client(timeout=8,follow_redirects=False) as client:
            response=client.get(settings.COW_HAL_BASE_URL.rstrip("/")+path,headers={
                "X-HAL-Timestamp":str(ts),"X-HAL-Signature":signature})
        if response.status_code!=200:
            raise CommandError(f"HAL returned HTTP {response.status_code}; cannot infer physical effect")
        data=response.json()
        self.stdout.write(f"CMS={c.state}, HAL={data.get('state','UNKNOWN')}; DO NOT equate queue acceptance with physical start")
