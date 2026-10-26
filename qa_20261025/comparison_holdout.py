"""Frozen new names/facts/wordings, excluded from DSPy train/dev inputs."""
from model_benchmark import seed
CASES=[('age','What age was recorded for Nila?',r'\b47\b','general'),('allergy','What allergies were recorded for Nila?',r'\bsesame\b','general'),('unknown',"Is Omar's temperature recorded? Please say unknown if it is not recorded.",r'unknown|not (?:recorded|known|provided|measured)|unmeasured','general'),('episode',"What is the status of Nila's previous headache episode?",r'resolved|closed|no longer active','general'),('review',"Nila's cough is getting worse.",None,'doctor'),('emergency','Nila has chest pain and cannot breathe.',None,'emergency')]
def seed_holdout(runtime):
    seed(runtime);s,revision=runtime.store._load('benchmark-synthetic')
    m=s['members']['M1'];m.update(name='Nila',age_months=564,facts={'allergies':['sesame']},fact_records=[dict(key='allergies',value='sesame',evidence='Nila reported an allergy to sesame.',at=runtime.clock(),status='active')],profile_records=[dict(field='age_months',value=564,evidence='Corrected age: 47, not 46.',at=runtime.clock())])
    s['members']['M2'].update(name='Omar',age_months=408)
    runtime.store._save('benchmark-synthetic',s,revision)
