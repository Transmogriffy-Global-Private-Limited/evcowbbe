"""C3 duty evidence is immutable at the PostgreSQL boundary.

The trigger only covers new C3 event rows and does not change prior C2 flows.
Reverse on an EMPTY disposable test schema only, never as a production rollback.
"""
from django.db import migrations


FORWARD = """
CREATE FUNCTION cow_c3_duty_event_immutable()
RETURNS trigger LANGUAGE plpgsql AS $body$
BEGIN
    RAISE EXCEPTION 'C3 duty history is append-only'
        USING ERRCODE = '23514';
END;
$body$;

CREATE TRIGGER cow_c3_duty_event_no_update_delete
BEFORE UPDATE OR DELETE ON cow_driverdutyevent
FOR EACH ROW EXECUTE FUNCTION cow_c3_duty_event_immutable();
"""

REVERSE = """
DROP TRIGGER IF EXISTS cow_c3_duty_event_no_update_delete ON cow_driverdutyevent;
DROP FUNCTION IF EXISTS cow_c3_duty_event_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("cow", "0014_driver_duty_c3")]
    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]
