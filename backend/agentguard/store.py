import json, sqlite3
from pathlib import Path
from pydantic import BaseModel
class Store:
 def __init__(self,path:str):
  self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.connect() as c:c.execute("CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT PRIMARY KEY,product_id TEXT,payload TEXT NOT NULL)")
 def connect(self):
  c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row;return c
 def save(self,kind:str,id:str,product_id:str,payload:BaseModel):
  with self.connect() as c:c.execute("INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,product_id=excluded.product_id",(kind,id,product_id,payload.model_dump_json()))
 def get(self,kind:str,id:str,model:type[BaseModel]):
  with self.connect() as c:r=c.execute("SELECT payload FROM records WHERE kind=? AND id=?",(kind,id)).fetchone()
  return model.model_validate_json(r["payload"]) if r else None
 def list(self,kind:str,model:type[BaseModel],product_id:str|None=None):
  q="SELECT payload FROM records WHERE kind=?";args=[kind]
  if product_id:q+=" AND product_id=?";args.append(product_id)
  with self.connect() as c:rows=c.execute(q,args).fetchall()
  return [model.model_validate_json(r["payload"]) for r in rows]
