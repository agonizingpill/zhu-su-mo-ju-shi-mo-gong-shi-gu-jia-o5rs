"""Maintenance life assessments and the completion timestamps they depend on.

New tables only: existing V3 tables keep their columns, so ``create_all`` stays
non-destructive on an existing database.
"""
from sqlalchemy import String,Float,Integer,ForeignKey,JSON,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
class MaintenanceCompletion(Base):
    __tablename__='maintenance_completions'
    __table_args__=(UniqueConstraint('window_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    window_id:Mapped[int]=mapped_column(ForeignKey('maintenance_windows.id'))
    mould_id:Mapped[int]=mapped_column(ForeignKey('moulds.id'))
    completed_at:Mapped[str]=mapped_column(String(40))
class MaintenanceAssessment(Base):
    __tablename__='maintenance_assessments'
    id:Mapped[int]=mapped_column(primary_key=True)
    mould_id:Mapped[int]=mapped_column(ForeignKey('moulds.id'))
    window_id:Mapped[int|None]=mapped_column(ForeignKey('maintenance_windows.id'),nullable=True)
    status:Mapped[str]=mapped_column(String(20),default='queued')
    continued_hours:Mapped[float]=mapped_column(Float)
    cutoff_at:Mapped[str]=mapped_column(String(40))
    snapshot:Mapped[dict]=mapped_column(JSON)
    source_fingerprint:Mapped[str]=mapped_column(String(64))
    result:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    error:Mapped[str]=mapped_column(String(600),default='')
    attempt:Mapped[int]=mapped_column(Integer,default=0)
    requested_by:Mapped[str]=mapped_column(String(120))
    requested_at:Mapped[str]=mapped_column(String(40))
    calculated_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
    approved_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
    approved_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
