import argparse,json,os
from .service import Service
def main(argv=None):
 p=argparse.ArgumentParser(prog="agentguard");p.add_argument("--db",default=os.getenv("AGENTGUARD_DB","data/agentguard.db"));p.add_argument("--format",choices=["text","json"],default="text");s=p.add_subparsers(dest="cmd",required=True);s.add_parser("init");prod=s.add_parser("product").add_subparsers(dest="sub",required=True);a=prod.add_parser("add");a.add_argument("--name",required=True);a.add_argument("--description",default="");prod.add_parser("list");g=prod.add_parser("get");g.add_argument("id");fix=s.add_parser("fixture").add_subparsers(dest="sub",required=True);fix.add_parser("load").add_argument("name",choices=["minimal"]);r=s.add_parser("report").add_subparsers(dest="sub",required=True);rp=r.add_parser("prepare");rp.add_argument("--product-id",required=True);x=p.parse_args(argv);v=Service(x.db)
 try:
  if x.cmd=="init":out={"db":x.db}
  elif x.cmd=="product" and x.sub=="add":q,w=v.create(x.name,x.description);out={"product":q.model_dump(),"version":w.model_dump()}
  elif x.cmd=="product" and x.sub=="list":out={"products":[z.model_dump() for z in v.products()]}
  elif x.cmd=="product":
   q=v.product(x.id)
   if not q:raise KeyError(x.id)
   out={"product":q.model_dump()}
  elif x.cmd=="fixture":out={"product":v.fixture().model_dump()}
  else:q,w=v.report(x.product_id);out={"eval_run":q.model_dump(),"findings":[],"release_decision":w.model_dump()}
  print(json.dumps({"ok":True,"data":out},ensure_ascii=False) if x.format=="json" else out);return 0
 except KeyError:
  e={"ok":False,"error":{"stage":"lookup","reason":"product not found","next_step":"create a product or load the fixture"}};print(json.dumps(e) if x.format=="json" else e);return 2
if __name__=="__main__":raise SystemExit(main())
