from agentguard.service import Service
def test_fixture_report_is_pending(tmp_path):
 s=Service(str(tmp_path/'a.db'));p=s.fixture();r,d=s.report(p.product_id);assert r.status=='created' and d.status=='pending'
def test_products_persist(tmp_path):
 s=Service(str(tmp_path/'a.db'));p,_=s.create('A');assert s.product(p.product_id).name=='A'
