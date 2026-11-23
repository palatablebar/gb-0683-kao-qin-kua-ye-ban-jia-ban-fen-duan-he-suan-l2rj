"""M8 加班预支 / M9 预支结清 裁决测试。"""
import csv,io,os,sqlite3
import pytest
from decimal import Decimal
from app.workflow import run,load
from app import advance_store
from app.errors import DomainError
from app.modules.m8_advance.service import execute_issue
from app.modules.m9_settlement.service import execute_settle,execute as settle_preview
from app.modules.m9_settlement.export_settlement import settlement_csv

MID={'as_of':'2026-10-16T00:00:00+08:00','period_key':'P-2026-10','holidays':[]}
MID_LATE=dict(MID);MID_LATE['as_of']='2026-10-16T03:00:00+08:00'
END={'period_key':'P-2026-10','holidays':[]}

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'ledger.db'))
    return advance_store

def issue(client_or_none=None):
    return execute_issue(run(load('midmonth_open.csv'),MID,8))

# ---------- M8 预支口径 ----------

def test_partial_advance_uses_only_completed_segments(db):
    s=run(load('midmonth_open.csv'),MID,8)
    lines={l['shift_id']:l for l in s['advance']['lines']}
    # E100 后半夜未闭合：只预付 22:00-24:00 两小时，不按整班 8 小时排班预付
    e100=lines['E100/2026-10-15']
    assert e100['basis']=='PARTIAL' and e100['open_shift'] is True
    assert e100['minutes']==120 and Decimal(e100['amount'])==Decimal('40.00')
    # E101 无 OUT 且 as_of 前无完整片段 -> 不生成预支行，绝不凭空预付
    assert 'E101/2026-10-15' not in lines
    assert s['advance']['summary']['skipped_open_no_segment']==1

def test_closed_shift_advance_pays_full_segments(db):
    # 截止时间晚于所有下班打卡 -> CLOSED 全额
    params=dict(MID);params['as_of']='2026-10-16T12:00:00+08:00'
    s=run(load('midmonth_open.csv'),params,8)
    e100=next(l for l in s['advance']['lines'] if l['shift_id']=='E100/2026-10-15')
    assert e100['basis']=='CLOSED' and e100['open_shift'] is False
    assert Decimal(e100['amount'])==Decimal('140.00')  # 7h*20

def test_later_cutoff_grows_partial_advance(db):
    early={l['shift_id']:l for l in run(load('midmonth_open.csv'),MID,8)['advance']['lines']}
    late={l['shift_id']:l for l in run(load('midmonth_open.csv'),MID_LATE,8)['advance']['lines']}
    assert Decimal(late['E100/2026-10-15']['amount'])>Decimal(early['E100/2026-10-15']['amount'])
    # 03:00 前 E100 完成 22-24(120m)+00-02(120m)，休息 02-03 不计
    assert late['E100/2026-10-15']['minutes']==240

def test_advance_requires_as_of(db):
    with pytest.raises(DomainError) as e:
        execute_issue(run(load('midmonth_open.csv'),{'period_key':'P-2026-10'},8))
    assert e.value.code=='PARAMETERS'

def test_advance_preview_does_not_persist(db):
    run(load('midmonth_open.csv'),MID,8)
    assert advance_store.list_advances('P-2026-10')==[]

def test_advance_persist_and_rerun_is_idempotent(db):
    a1=issue();a2=issue()
    assert a1['advance']['created'] is True and a2['advance']['created'] is False
    assert a1['advance']['advance_id']==a2['advance']['advance_id']
    assert len(advance_store.list_advances('P-2026-10'))==1

def test_different_as_of_creates_distinct_advance(db):
    issue()
    second=execute_issue(run(load('midmonth_open.csv'),MID_LATE,8))
    assert second['advance']['created'] is True
    assert len(advance_store.list_advances('P-2026-10'))==2

def test_advance_line_evidence_references_source(db):
    doc=advance_store.get_advance_doc(issue()['advance']['advance_id'])
    line=next(l for l in doc['lines'] if l['shift_id']=='E100/2026-10-15')
    evidence=__import__('json').loads(line['evidence'])
    assert all(e['minutes']>0 and e['amount'] for e in evidence)
    assert sum(e['minutes'] for e in evidence)==line['minutes']

# ---------- M9 结清逐项对应 ----------

@pytest.fixture
def settled(db):
    issue()
    execute_issue(run(load('midmonth_open.csv'),MID_LATE,8))
    return execute_settle(run(load('monthend_full.csv'),END,9))['settlement']

def test_settlement_line_item_correspondence(settled):
    by_emp={l['employee']:l for l in settled['lines']}
    e102=by_emp['E102']
    # 两次预支合计 200，最终 187.50 -> 待扣回 12.50，净付 -12.50
    assert Decimal(e102['advance_amount'])==Decimal('200.00')
    assert Decimal(e102['final_amount'])==Decimal('187.50')
    assert Decimal(e102['clawback'])==Decimal('12.50')
    assert Decimal(e102['net_payable'])==Decimal('-12.50')
    assert len(e102['advance_ids'])==2
    e101=by_emp['E101']  # 月中无预支、月底补卡闭合
    assert Decimal(e101['advance_amount'])==0 and Decimal(e101['final_amount'])==Decimal('140.00')
    assert Decimal(e101['net_payable'])==Decimal('140.00')

def test_settlement_identity_and_balance(settled):
    assert settled['balanced'] is True
    assert all(c['ok'] for c in settled['checks'])
    f=Decimal(settled['final_amount']);a=Decimal(settled['advance_total'])
    cb=Decimal(settled['clawback']);pay=Decimal(settled['payable'])
    assert f+cb==a+pay

def test_settlement_rerun_is_idempotent_no_duplicate(db,settled):
    again=execute_settle(run(load('monthend_full.csv'),END,9))['settlement']
    assert again['created'] is False
    assert [s['version'] for s in advance_store.list_settlements('P-2026-10')]==[1]

def test_changed_source_supersedes_old_advances_and_settlement(db,settled):
    all_advances=advance_store.list_advances('P-2026-10')
    old_ids=[a['advance_id'] for a in all_advances]
    prior_status={a['advance_id']:a['status'] for a in all_advances}
    v2=execute_settle(run(load('monthend_changed.csv'),END,9))['settlement']
    assert v2['version']==2 and v2['balanced'] is True
    rows=advance_store.list_settlements('P-2026-10')
    assert [(r['version'],r['status']) for r in rows]==[(1,'SUPERSEDED'),(2,'CURRENT')]
    # 预支状态在重算中保持一致：同源被取代的保持 SUPERSEDED，其余随新结清重新指向
    for aid in old_ids:
        doc=advance_store.get_advance_doc(aid)
        if prior_status[aid]=='SUPERSEDED':
            assert doc['status']=='SUPERSEDED'  # 首次取代关系冻结，仍可追溯
        else:
            assert doc['status']=='SETTLED' and doc['settled_by']==v2['settlement_id']
        assert advance_store.get_snapshot(doc['source_sha']) is not None
    # 旧预支已发金额仍在新结清链条里，不丢账、不重复支付
    assert Decimal(v2['advance_total'])==Decimal(settled['advance_total'])
    e102=next(l for l in v2['lines'] if l['employee']=='E102')  # 时薪 25->30
    assert Decimal(e102['final_amount'])==Decimal('225.00') and Decimal(e102['clawback'])==0

def test_same_source_newer_advance_marks_old_one_superseded(db):
    # 补卡后用“同一来源”重发预支：旧预支失效重算并标记 SUPERSEDED
    first=issue()
    second=execute_issue(run(load('midmonth_open.csv'),MID_LATE,8))  # 同源、更晚 as_of
    end={'period_key':'P-2026-10','holidays':[]}
    doc=execute_settle(run(load('monthend_full.csv'),end,9))['settlement']
    statuses={a['advance_id']:a['advance_status'] for a in doc['advances']}
    assert statuses[first['advance']['advance_id']]=='SUPERSEDED'
    assert statuses[second['advance']['advance_id']]=='SETTLED'
    stored=advance_store.get_advance_doc(first['advance']['advance_id'])
    assert stored['status']=='SUPERSEDED' and stored['superseded_by']==doc['settlement_id']
    # 两版预支金额都在扣回链条，逐行可对应
    e100=next(l for l in doc['lines'] if l['employee']=='E100')
    assert set(e100['advance_ids'])=={first['advance']['advance_id'],second['advance']['advance_id']}

def test_settlement_generation_failure_restores_previous_version(db,settled,monkeypatch):
    real=advance_store.connection
    class Boom:
        def __init__(self,r):self.r=r
        def execute(self,sql,*a,**k):
            if sql.lstrip().startswith('INSERT INTO settlement_lines'):
                raise RuntimeError('disk failure')
            return self.r.execute(sql,*a,**k)
        def commit(self):return self.r.commit()
        def rollback(self):return self.r.rollback()
        def close(self):return self.r.close()
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def __getattr__(self,n):return getattr(self.r,n)
    monkeypatch.setattr(advance_store,'connection',lambda:Boom(real()))
    with pytest.raises(RuntimeError):
        execute_settle(run(load('monthend_changed.csv'),END,9))
    monkeypatch.setattr(advance_store,'connection',real)  # 仅恢复连接，保留 DB_PATH
    rows=advance_store.list_settlements('P-2026-10')
    assert [(r['version'],r['status']) for r in rows]==[(1,'CURRENT')]
    intact=advance_store.get_settlement_doc(settled['settlement_id'])
    assert len(intact['lines'])==len(settled['lines'])
    # 失败后重试成功
    retry=execute_settle(run(load('monthend_changed.csv'),END,9))['settlement']
    assert retry['version']==2 and retry['status']=='CURRENT'

def test_unbalanced_cannot_seal_or_export(db,settled):
    con=sqlite3.connect(os.environ['DB_PATH'])
    con.execute('UPDATE settlements SET balanced=0 WHERE settlement_id=?',(settled['settlement_id'],))
    con.commit();con.close()
    with pytest.raises(DomainError) as e:
        advance_store.mark_sealed(settled['settlement_id'])
    assert e.value.status==409 and e.value.code=='UNBALANCED'

def test_seal_only_after_balanced(db,settled):
    assert advance_store.mark_sealed(settled['settlement_id']) is True
    assert advance_store.mark_sealed(settled['settlement_id']) is False  # 重复封存幂等

def test_export_matches_stored_version(db,settled):
    text=settlement_csv(settled).lstrip('﻿')
    rows=list(csv.reader(io.StringIO(text)))
    details=[r for r in rows if r[0]=='结清明细']
    assert len(details)==len(settled['lines'])
    joined='\n'.join(','.join(r) for r in rows)
    for code in ('结清抬头','对平核查','预支依据','结清明细','员工汇总','事件留痕'):
        assert code in joined

def test_source_snapshots_never_lost(db,settled):
    import base64
    mid_sha=advance_store.get_advance_doc(settled['advances'][0]['advance_id'])['source_sha']
    assert base64.b64decode(advance_store.get_snapshot(mid_sha)['source_b64'])==load('midmonth_open.csv')
    assert base64.b64decode(advance_store.get_snapshot(settled['source_sha'])['source_b64'])==load('monthend_full.csv')

def test_advance_with_zero_advance_period_still_settles(db):
    # 全新期间、无任何预支：结清仍平衡，净付=最终应付
    params={'period_key':'EMPTY','holidays':[]}
    doc=execute_settle(run(load('monthend_full.csv'),params,9))['settlement']
    assert doc['balanced'] is True
    assert Decimal(doc['advance_total'])==0
    assert Decimal(doc['net_payable'])==Decimal(doc['final_amount'])

# ---------- HTTP 入口 ----------

def test_http_full_flow(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'http.db'))
    from app.main import create_app
    app=create_app();app.config['TESTING']=True
    c=app.test_client()
    assert c.get('/m8_advance').status_code==200 and c.get('/m9_settlement').status_code==200
    r=c.post('/api/advances',json={'sample':'midmonth_open.csv','parameters':MID})
    assert r.status_code==200 and r.json['created'] is True
    assert c.post('/api/advances',json={'sample':'midmonth_open.csv','parameters':MID}).json['created'] is False
    s=c.post('/api/settlements',json={'sample':'monthend_full.csv','parameters':END})
    assert s.status_code==200 and s.json['balanced'] is True
    sid=s.json['settlement_id']
    assert c.get('/api/settlements/%s/export.csv'%sid).status_code==200
    page=c.get('/m9_settlement').data.decode()
    assert sid in page and s.json['final_amount'] in page  # 页面与导出同版
    assert c.post('/api/settlements/%s/seal'%sid).status_code==200

def test_http_export_blocked_when_unbalanced(tmp_path,monkeypatch,settled):
    from app.main import create_app
    app=create_app();app.config['TESTING']=True
    c=app.test_client()
    con=sqlite3.connect(os.environ['DB_PATH'])
    con.execute('UPDATE settlements SET balanced=0 WHERE settlement_id=?',(settled['settlement_id'],))
    con.commit();con.close()
    r=c.get('/api/settlements/%s/export.csv'%settled['settlement_id'])
    assert r.status_code==409 and r.json['code']=='UNBALANCED'
