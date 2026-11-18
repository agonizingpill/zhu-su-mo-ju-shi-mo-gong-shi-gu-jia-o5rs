import time,json,logging
from pathlib import Path
from .db import Session
from .models import Estimate,Mould
from .ml import predict_trial
from .queue import r,ESTIMATE_QUEUE,ASSESSMENT_QUEUE
from . import maintenance_jobs
Path('/tmp/worker-heartbeat').touch()
_last_sweep=0.0
while True:
    Path('/tmp/worker-heartbeat').touch()
    # A queued assessment whose queue entry was lost to a worker interruption
    # is re-queued; re-running it derives the same conclusion from the frozen
    # snapshot, so this resumes work without creating a new assessment.
    if time.time()-_last_sweep>10:
        try:maintenance_jobs.sweep(r)
        except Exception:logging.exception('assessment sweep failed')
        _last_sweep=time.time()
    entry=r.blpop([ESTIMATE_QUEUE,ASSESSMENT_QUEUE],timeout=2)
    if not entry: continue
    if entry[0]==ASSESSMENT_QUEUE:
        maintenance_jobs.process(int(entry[1]))
        continue
    eid=int(entry[1])
    with Session() as s:
        e=s.get(Estimate,eid)
        if not e or e.status!='calculating':continue
        try:
            e.predicted_hours,e.model_result=predict_trial(s,s.get(Mould,e.mould_id),e.setup_hours)
            e.status='estimated'
            Path(f'/artifacts/estimate-{eid}.json').write_text(json.dumps(e.model_result,ensure_ascii=False))
        except Exception as ex:
            logging.exception('estimate failed');e.status='failed';e.model_result={'error':str(ex)}
        s.commit()
