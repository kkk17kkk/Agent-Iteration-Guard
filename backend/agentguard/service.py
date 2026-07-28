from .domain import *
from .store import Store
class Service:
 def __init__(self,db:str):self.store=Store(db)
 def create(self,name:str,description:str=""):
  p=Product(name=name,description=description);v=Version(product_id=p.product_id,label="initial");p.current_version_id=v.version_id;self.store.save("product",p.product_id,p.product_id,p);self.store.save("version",v.version_id,p.product_id,v);return p,v
 def products(self):return self.store.list("product",Product)
 def product(self,id:str):return self.store.get("product",id,Product)
 def fixture(self):
  p,v=self.create("Iteration Guard Demo","Fixed Phase 1 fixture");r=Requirement(product_id=p.product_id,title="Complete deterministic tool task");c=Capability(product_id=p.product_id,name="Tool execution",requirement_ids=[r.requirement_id]);e=EvalCase(product_id=p.product_id,name="Expected state",capability_ids=[c.capability_id])
  for k,x,i in [("requirement",r,r.requirement_id),("capability",c,c.capability_id),("eval_case",e,e.eval_case_id)]:self.store.save(k,i,p.product_id,x)
  return p
 def report(self,id:str):
  p=self.product(id)
  if not p:raise KeyError(id)
  cases=self.store.list("eval_case",EvalCase,id);run=EvalRun(product_id=id,version_id=p.current_version_id,eval_case_ids=[x.eval_case_id for x in cases]);d=ReleaseDecision(product_id=id,version_id=p.current_version_id,rationale="No evaluation has run; release readiness is unresolved.")
  self.store.save("eval_run",run.eval_run_id,id,run);self.store.save("release_decision",d.decision_id,id,d);return run,d
