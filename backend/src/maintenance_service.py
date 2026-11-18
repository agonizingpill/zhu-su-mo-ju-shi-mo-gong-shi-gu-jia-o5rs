from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select
from .models import Estimate,Mould
from .execution_models import TrialExecution
from .workshop_models import MaintenanceWindow
from .maintenance_models import MaintenanceCompletion,MaintenanceAssessment
from .maintenance_life import fingerprint
from .services import item,serialize,record

def now():
    return datetime.now(timezone.utc).isoformat()

def visible_records(s,mould_id):
    """All completed trial actuals and completed inspections currently visible.

    Used both for the cut-off snapshot and for the staleness fingerprint, so a
    late back-filled actual is detected even when its business timestamp is
    older than an earlier assessment's cut-off.
    """
    rows=s.scalars(select(TrialExecution).join(Estimate,TrialExecution.estimate_id==Estimate.id).where(Estimate.mould_id==mould_id,TrialExecution.status.in_(['completed','settled']),TrialExecution.ended_at.is_not(None))).all()
    executions=[{'id':x.id,'ended_at':x.ended_at,'hours':x.actual_hours} for x in rows]
    completions=[{'window_id':c.window_id,'completed_at':c.completed_at} for c in s.scalars(select(MaintenanceCompletion).where(MaintenanceCompletion.mould_id==mould_id))]
    return executions,completions

def current_fingerprint(s,mould_id):
    executions,completions=visible_records(s,mould_id)
    return fingerprint(executions,completions)

def snapshot_for(s,mould_id,now_stamp):
    # The cut-off is the wall-clock instant the request is made.  Everything
    # visible in the database at that instant is frozen, including records with
    # back-dated business timestamps.  Anything inserted later — however old its
    # business date — is invisible to this assessment and only enters a new one.
    executions,completions=visible_records(s,mould_id)
    return {
        'mould_id':mould_id,
        'cutoff_at':now_stamp,
        'executions':executions,
        'completions':completions,
    }

def effective_status(s,a):
    """A ready/blocked conclusion turns stale once its frozen evidence changes."""
    if a.status in ('ready','blocked') and current_fingerprint(s,a.mould_id)!=a.source_fingerprint:
        return 'stale'
    return a.status

def view(s,a):
    d=serialize(a)
    # The frozen raw snapshot and full result are response-heavy; the page gets
    # the derived evidence plus the counts that prove what was frozen.
    d.pop('snapshot',None);d.pop('result',None)
    d['effective_status']=effective_status(s,a)
    d['cutoff_at']=a.cutoff_at
    if d['effective_status']=='stale':
        d['stale_reason']='评估后试模负荷或完检记录已变化，原结论不可用于批准，请重新评估'
    else:
        d['stale_reason']=''
    if a.result:
        d['blocking_reasons']=a.result.get('blocking_reasons',[])
        d['sample']=a.result.get('sample')
        d['probability']=a.result.get('probability')
        d['current_load_hours']=a.result.get('current_load_hours')
        d['intervals']=[i for i in a.result.get('intervals',[]) if i.get('kind')=='event']
        d['open_interval']=next((i for i in a.result.get('intervals',[]) if i.get('kind')=='censored'),None)
    else:
        d['blocking_reasons']=['评估计算尚未完成']
        d['sample']=None;d['probability']=None;d['current_load_hours']=None
        if a.status=='failed':d['blocking_reasons']=['评估计算失败：'+(a.error or '未知错误')]
    d['evidence']={
        'method':(a.result or {}).get('method','Weibull MLE / right-censored'),
        'shape':(a.result or {}).get('shape'),
        'scale_hours':(a.result or {}).get('scale_hours'),
        'survival_now':(a.result or {}).get('survival_now'),
        'survival_after':(a.result or {}).get('survival_after'),
        'horizon_load_hours':(a.result or {}).get('horizon_load_hours'),
        'note':(a.result or {}).get('note',''),
        'snapshot_executions':len(a.snapshot.get('executions',[])),
        'snapshot_completions':len(a.snapshot.get('completions',[])),
        'fingerprint':a.source_fingerprint[:16],
    }
    if a.status=='approved':d['blocking_reasons']=[]
    return d

def _queue(s,r,a):
    # The marker prevents the periodic sweep from duplicating a queue entry
    # while the original one is still waiting.  It expires so an interrupted
    # worker's request is re-queued by the next sweep and resumes the same row.
    if not r.set(f'maintenance-inflight:{a.id}',1,nx=True,ex=30):
        return True
    try:r.rpush('maintenance-assessments',a.id)
    except Exception:
        # The assessment stays queued and the worker sweep re-enqueues it.
        return False
    return True

def create(s,redis,p,u):
    candidate=item(s,MaintenanceWindow,p.window_id)
    # Lock the mould before the window, the same order as begin/approve, so a
    # concurrent trial start cannot slip between reading the load and freezing
    # the snapshot.
    s.scalar(select(Mould).where(Mould.id==candidate.mould_id).with_for_update())
    w=s.scalar(select(MaintenanceWindow).where(MaintenanceWindow.id==p.window_id).with_for_update())
    if not w:raise HTTPException(404,'保养窗口不存在')
    if w.state!='planned':raise HTTPException(409,'仅待批准保养计划可做寿命评估')
    stamp=now();snap=snapshot_for(s,w.mould_id,stamp)
    fp=fingerprint(snap['executions'],snap['completions'])
    # A retried request (same window, same asked hours, same visible records)
    # resumes the original assessment instead of creating a new one.
    existing=s.scalar(select(MaintenanceAssessment).where(MaintenanceAssessment.window_id==w.id,MaintenanceAssessment.continued_hours==p.continued_hours,MaintenanceAssessment.source_fingerprint==fp,MaintenanceAssessment.status.in_(['queued','ready','blocked','failed'])).order_by(MaintenanceAssessment.id.desc()))
    if existing:
        if existing.status=='failed':
            existing.status='queued';existing.error='';record(s,u,'重新投递保养评估 '+str(existing.id))
            r.delete(f'maintenance-inflight:{existing.id}')
        _queue(s,redis,existing)
        return view(s,existing)
    # Previous unapproved conclusions for this window are superseded, not
    # silently overwritten: they stay as audit evidence marked 待重评.
    for old in s.scalars(select(MaintenanceAssessment).where(MaintenanceAssessment.window_id==w.id,MaintenanceAssessment.status.in_(['queued','ready','blocked','failed']))):
        old.status='stale'
    a=MaintenanceAssessment(mould_id=w.mould_id,window_id=w.id,status='queued',continued_hours=p.continued_hours,cutoff_at=stamp,snapshot=snap,source_fingerprint=fp,attempt=0,requested_by=u.email,requested_at=stamp)
    s.add(a);s.flush();record(s,u,'发起保养寿命评估 '+str(a.id));_queue(s,redis,a);return view(s,a)

def retry(s,redis,id,u):
    a=s.scalar(select(MaintenanceAssessment).where(MaintenanceAssessment.id==id).with_for_update())
    if not a:raise HTTPException(404,'评估不存在')
    if a.status!='failed':raise HTTPException(409,'仅计算失败的评估可重试，且沿用原截止时点与证据')
    a.status='queued';a.error='';record(s,u,'重试保养评估 '+str(a.id))
    redis.delete(f'maintenance-inflight:{a.id}')
    _queue(s,redis,a);return view(s,a)

def require_ready(s,w,p,a):
    if not a or a.window_id!=w.id:raise HTTPException(409,'保养批准须绑定本窗口的寿命评估')
    if a.status in ('ready','blocked') and current_fingerprint(s,w.mould_id)!=a.source_fingerprint:
        # The raised error rolls back this approval transaction, so the stale
        # state is not committed here.  `effective_status` re-derives it from the
        # fingerprint on every view, so the page immediately shows 待重评.
        a.status='stale'
        raise HTTPException(409,'评估后试模负荷或完检已变，原结论失效，请重新评估后再批准')
    if a.status!='ready':
        if a.status=='blocked':
            reasons=(a.result or {}).get('blocking_reasons') or ['样本或参数不满足评估条件']
            raise HTTPException(409,'评估被拦截，不能批准：'+'；'.join(reasons))
        if a.status=='stale':raise HTTPException(409,'评估已待重评，请重新评估后再批准')
        raise HTTPException(409,'评估尚未完成，不能批准')
    if not a.result or a.result.get('probability') is None:raise HTTPException(409,'评估尚无有效概率，不能批准')
    return a
