"""Background execution of maintenance-life assessments.

The job is derived entirely from what was frozen when the request was made
(``snapshot`` + ``continued_hours``), so an interrupted calculation can simply
be run again and produces the same conclusion.  Any records that arrived after
the cut-off are picked up only by the *next* assessment, never merged in.
"""
import json,logging
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy import select
from .db import Session
from .maintenance_models import MaintenanceAssessment
from .maintenance_life import assess

PATH_ROOT=Path('/artifacts')

def _finish(s,a,result):
    # The database row is the source of truth; the shared artifact volume is a
    # convenience copy and must not turn a successful fit into a failed job.
    a.result=result
    a.status=result['status']
    a.calculated_at=datetime.now(timezone.utc).isoformat()
    a.error=''
    try:
        PATH_ROOT.mkdir(exist_ok=True)
        (PATH_ROOT/f'assessment-{a.id}.json').write_text(json.dumps(result,ensure_ascii=False))
    except OSError:
        logging.warning('assessment artifact not written',exc_info=True)

def process(assessment_id:int)->None:
    """Run (or re-run) one assessment; safe to call repeatedly for a request."""
    with Session() as s:
        a=s.scalar(select(MaintenanceAssessment).where(MaintenanceAssessment.id==assessment_id).with_for_update())
        if not a:return
        if a.status in ('ready','blocked','approved','stale'):
            # Already concluded by this request, or superseded: do not create a
            # second conclusion or overwrite an approved one.
            return
        a.attempt=(a.attempt or 0)+1
        try:
            _finish(s,a,assess(a.snapshot,a.continued_hours))
        except Exception as ex:
            logging.exception('maintenance assessment failed')
            a.status='failed';a.error=str(ex)[:600]
        s.commit()

def sweep(queue)->int:
    """Resume requests whose queue entry was lost by a worker interruption."""
    with Session() as s:
        ids=list(s.scalars(select(MaintenanceAssessment.id).where(MaintenanceAssessment.status=='queued').order_by(MaintenanceAssessment.id)))
    if not ids:return 0
    try:pending={str(x) for x in queue.lrange('maintenance-assessments',0,-1)}
    except Exception:pending=set()
    queued=0
    for i in ids:
        if str(i) in pending:continue
        try:queue.rpush('maintenance-assessments',i);queued+=1
        except Exception:logging.exception('assessment sweep enqueue failed')
    return queued
