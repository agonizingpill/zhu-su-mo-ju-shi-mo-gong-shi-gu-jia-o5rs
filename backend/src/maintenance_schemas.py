from pydantic import BaseModel,Field,ConfigDict
class Clean(BaseModel):model_config=ConfigDict(str_strip_whitespace=True)
class AssessmentInput(Clean):
    window_id:int=Field(gt=0)
    continued_hours:float=Field(gt=0,le=10000)
class ApproveInput(Clean):
    assessment_id:int=Field(gt=0)
