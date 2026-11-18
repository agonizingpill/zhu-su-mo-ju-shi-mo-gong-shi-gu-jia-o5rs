import random
from datetime import datetime,timedelta
from sqlalchemy import select, func
from src.db import Base,engine,Session
from src.models import User,Mould,MachineSlot,Estimate,TrialHistory
from src.execution_models import TrialExecution,CostReview
from src.workshop_models import MaintenanceWindow,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery
from src.maintenance_models import MaintenanceCompletion,MaintenanceAssessment
from src.auth import hash_password

# Seven completed inspections on MD-115 give the life model observed cycles,
# plus one open cycle for the current cumulative load (right-censored).
LIFE_CYCLES=[('2026-06-05T09:00:00+08:00',13.0),('2026-06-24T09:00:00+08:00',27.0),('2026-07-12T09:00:00+08:00',16.0),('2026-07-30T09:00:00+08:00',31.0),('2026-08-18T09:00:00+08:00',18.0),('2026-09-06T09:00:00+08:00',25.0),('2026-09-25T09:00:00+08:00',14.0)]
LIFE_OPEN=('2026-10-14T09:00:00+08:00',19.0)

def seed_life(s):
    from src.execution_service import stamp as convert
    mould=s.scalar(select(Mould).where(Mould.code=='MD-115'))
    if not mould:return
    stop=convert(datetime.fromisoformat(LIFE_OPEN[0]))
    for i,(done,load) in enumerate(LIFE_CYCLES,1):
        end=convert(datetime.fromisoformat(done));start=end-timedelta(hours=4)
        w=s.scalar(select(MaintenanceWindow).where(MaintenanceWindow.mould_id==mould.id,MaintenanceWindow.starts_at==start.isoformat()))
        if not w:
            w=MaintenanceWindow(mould_id=mould.id,starts_at=start.isoformat(),ends_at=end.isoformat(),reason=f'LIFE-DEMO-C{i:02d} 顶针润滑、模面清洁与滑块检查',state='completed',approved_by='admin@ruimo.demo',inspection_note='模面与顶出动作检查正常，记录停机前累计试模工时。')
            s.add(w);s.flush()
        if not s.scalar(select(MaintenanceCompletion).where(MaintenanceCompletion.window_id==w.id)):
            s.add(MaintenanceCompletion(window_id=w.id,mould_id=mould.id,completed_at=end.isoformat()))
        code=f'QE-LIFE-{i:02d}'
        if s.scalar(select(Estimate).where(Estimate.code==code)):continue
        ended=end-timedelta(hours=2);began=ended-timedelta(hours=load)
        e=Estimate(code=code,mould_id=mould.id,rate=280,setup_hours=1,predicted_hours=load,quote_amount=8000,status='committed')
        s.add(e);s.flush()
        s.add(TrialExecution(estimate_id=e.id,started_at=began.isoformat(),ended_at=ended.isoformat(),actual_hours=load,good_shots=90,total_shots=100,snapshot={'estimate_code':code,'predicted_hours':load,'quoted_amount':8000,'rate':280,'mould_code':'MD-115','slot_id':None},status='settled'))
    # One open cycle: executed after the last inspection and still unmaintained.
    end=stop-timedelta(hours=2);load=LIFE_OPEN[1];began=end-timedelta(hours=load)
    code='QE-LIFE-08'
    if not s.scalar(select(Estimate).where(Estimate.code==code)):
        e=Estimate(code=code,mould_id=mould.id,rate=280,setup_hours=1,predicted_hours=load,quote_amount=8000,status='committed')
        s.add(e);s.flush()
        s.add(TrialExecution(estimate_id=e.id,started_at=began.isoformat(),ended_at=end.isoformat(),actual_hours=load,good_shots=90,total_shots=100,snapshot={'estimate_code':code,'predicted_hours':load,'quoted_amount':8000,'rate':280,'mould_code':'MD-115','slot_id':None},status='settled'))


def seed():
    Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('admin','estimator','viewer'):
            if not s.scalar(select(User).where(User.email==role+'@ruimo.demo')): s.add(User(email=role+'@ruimo.demo',role=role,password_hash=hash_password('Mould2026!')))
        for code,name,cav,slides,steel,kg in [('MD-86','八腔医疗接头模',8,4,'S136',860),('MD-42','双腔仪表外壳模',2,2,'P20',420),('MD-115','十六腔盖件模',16,6,'H13',1150)]:
            if not s.scalar(select(Mould).where(Mould.code==code)): s.add(Mould(code=code,name=name,cavities=cav,slides=slides,steel=steel,weight_kg=kg))
        s.flush()
        for machine,start,cap in [('M280','2026-11-18 08:00',48),('M280','2026-11-19 08:00',4),('M450','2026-11-18 08:00',72)]:
            if not s.scalar(select(MachineSlot).where(MachineSlot.machine==machine,MachineSlot.start_hour==start)): s.add(MachineSlot(machine=machine,start_hour=start,capacity_hours=cap,reserved_hours=0))
        rng=random.Random(42)
        for i in range(160):
            code=f'TR-{i+1:03d}'
            if s.scalar(select(TrialHistory).where(TrialHistory.trial_code==code)):continue
            cav=rng.choice([2,4,8,16]);sl=rng.randint(0,8);kg=rng.uniform(180,1600);steel=rng.choice(['P20','H13','S136'])
            hours=2+cav*.3+sl*.65+kg*.004+cav*sl*.035+(['P20','H13','S136'].index(steel))*.8+rng.gauss(0,.4)
            s.add(TrialHistory(trial_code=code,cavities=cav,slides=sl,steel=steel,weight_kg=round(kg,1),actual_hours=round(hours,2)))
        s.flush()
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-26017')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-86'))
            s.add(Estimate(code='QE-26017',mould_id=m.id,rate=280,setup_hours=2,quote_amount=8000,status='draft'))
        s.flush()
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-FLOOR-DEMO')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-42'));slot=s.scalar(select(MachineSlot).where(MachineSlot.machine=='M450'))
            s.add(Estimate(code='QE-FLOOR-DEMO',mould_id=m.id,slot_id=slot.id,rate=300,setup_hours=1,predicted_hours=10,quote_amount=5000,status='committed',model_result={'note':'已承诺的演示订单，供实绩采集；不作模型效果指标'}));slot.reserved_hours+=10
        for code,grade,qty,price,state in [('RS-P20-DEMO','ABS自然色',500,20,'available'),('RS-ISOLATED','ABS复检隔离',50,18,'quarantine')]:
            if not s.scalar(select(ResinLot).where(ResinLot.code==code)):
                s.add(ResinLot(code=code,grade=grade,received_kg=qty,available_kg=qty,unit_price=price,state=state))
        seed_life(s)
        s.commit()
        print({t.__tablename__:s.scalar(select(func.count()).select_from(t)) for t in (User,Mould,MachineSlot,Estimate,TrialHistory,TrialExecution,CostReview,MaintenanceWindow,MaintenanceCompletion,MaintenanceAssessment,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery)})
if __name__=='__main__':seed()
