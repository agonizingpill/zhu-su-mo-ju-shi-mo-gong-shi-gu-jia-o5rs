from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from .db import db_session
from .auth import current,writers,admin
from .maintenance_models import MaintenanceAssessment
from .maintenance_schemas import AssessmentInput
from . import maintenance_service as svc
from .queue import r

router=APIRouter()
@router.get('/maintenance-assessments')
def assessments(s=Depends(db_session),u=Depends(current)):
    return [svc.view(s,x) for x in s.scalars(select(MaintenanceAssessment).order_by(MaintenanceAssessment.id.desc()))]
@router.get('/maintenance-assessments/{id}')
def assessment(id:int,s=Depends(db_session),u=Depends(current)):
    x=s.get(MaintenanceAssessment,id)
    if not x:raise HTTPException(404,'评估不存在')
    return svc.view(s,x)
@router.post('/maintenance-assessments')
def create(p:AssessmentInput,s=Depends(db_session),u=Depends(writers)):
    x=svc.create(s,r,p,u);s.commit();return x
@router.post('/maintenance-assessments/{id}/retry')
def retry(id:int,s=Depends(db_session),u=Depends(writers)):
    x=svc.retry(s,r,id,u);s.commit();return x
