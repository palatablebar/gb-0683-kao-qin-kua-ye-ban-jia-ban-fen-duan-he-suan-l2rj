import sqlite3,json,hashlib
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import os
from app.errors import DomainError

def _now():
    return datetime.now(timezone.utc).isoformat()

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('''CREATE TABLE IF NOT EXISTS advances(
        advance_id TEXT PRIMARY KEY, period_key TEXT NOT NULL, run_id TEXT NOT NULL,
        source_sha TEXT NOT NULL, as_of TEXT NOT NULL, basis TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ACTIVE', amount TEXT NOT NULL,
        currency TEXT NOT NULL DEFAULT 'CNY', created_at TEXT NOT NULL,
        superseded_by TEXT, settled_by TEXT, body TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS advance_lines(
        advance_id TEXT NOT NULL, employee TEXT NOT NULL, shift_id TEXT NOT NULL,
        shift_day TEXT NOT NULL, basis TEXT NOT NULL, open_shift INTEGER NOT NULL,
        minutes INTEGER NOT NULL, amount TEXT NOT NULL, evidence TEXT NOT NULL,
        PRIMARY KEY(advance_id,employee,shift_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS settlements(
        settlement_id TEXT PRIMARY KEY, period_key TEXT NOT NULL,
        version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'CURRENT',
        source_sha TEXT NOT NULL, run_id TEXT NOT NULL,
        final_amount TEXT NOT NULL, advance_total TEXT NOT NULL,
        clawback TEXT NOT NULL, net_payable TEXT NOT NULL,
        balanced INTEGER NOT NULL, sealed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, body TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS settlement_advances(
        settlement_id TEXT NOT NULL, advance_id TEXT NOT NULL,
        advance_status TEXT NOT NULL, amount TEXT NOT NULL,
        PRIMARY KEY(settlement_id,advance_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS settlement_lines(
        settlement_id TEXT NOT NULL, employee TEXT NOT NULL, shift_id TEXT NOT NULL,
        final_amount TEXT NOT NULL, advance_amount TEXT NOT NULL,
        clawback TEXT NOT NULL, net_payable TEXT NOT NULL,
        advance_ids TEXT NOT NULL, evidence TEXT NOT NULL,
        PRIMARY KEY(settlement_id,employee,shift_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS source_snapshots(
        source_sha TEXT PRIMARY KEY, source_b64 TEXT NOT NULL,
        parameters TEXT NOT NULL, saved_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS settlement_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, settlement_id TEXT,
        advance_id TEXT, event TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL)''')
    return db

# ---------- advances ----------

def find_advance(advance_id):
    with connection() as db:
        r=db.execute('SELECT * FROM advances WHERE advance_id=?',(advance_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','预支单不存在',404)
    return dict(r)

def list_advances(period_key=None):
    with connection() as db:
        if period_key:
            rows=db.execute('SELECT advance_id,period_key,run_id,source_sha,as_of,basis,status,amount,superseded_by,settled_by,created_at FROM advances WHERE period_key=? ORDER BY rowid',(period_key,)).fetchall()
        else:
            rows=db.execute('SELECT advance_id,period_key,run_id,source_sha,as_of,basis,status,amount,superseded_by,settled_by,created_at FROM advances ORDER BY rowid').fetchall()
    return [dict(r) for r in rows]

def advance_lines(advance_id):
    with connection() as db:
        rows=db.execute('SELECT * FROM advance_lines WHERE advance_id=? ORDER BY rowid',(advance_id,)).fetchall()
    return [dict(r) for r in rows]

def active_advances(period_key):
    with connection() as db:
        rows=db.execute("SELECT advance_id FROM advances WHERE period_key=? AND status='ACTIVE' ORDER BY rowid",(period_key,)).fetchall()
    return [r['advance_id'] for r in rows]

def save_snapshot(db, source_sha, source_b64, parameters):
    db.execute('INSERT OR IGNORE INTO source_snapshots(source_sha,source_b64,parameters,saved_at) VALUES (?,?,?,?)',
        (source_sha,source_b64,json.dumps(parameters or {},ensure_ascii=False),_now()))

def create_advance(doc):
    """Idempotent: same (period_key, run_id, as_of, basis) input reuses the stored advance."""
    with connection() as db:
        existing=db.execute('SELECT advance_id FROM advances WHERE period_key=? AND run_id=? AND as_of=? AND basis=?',
            (doc['period_key'],doc['run_id'],doc['as_of'],doc['basis'])).fetchone()
        if existing:
            db.execute("INSERT INTO settlement_events(settlement_id,advance_id,event,detail,at) VALUES(NULL,?,?,'重跑同一预支，复用未新增',?)",
                (existing['advance_id'],'ADVANCE_REUSE',_now()))
            return existing['advance_id'],False
        advance_id=doc['advance_id']
        save_snapshot(db,doc['source_sha'],doc['source_b64'],doc['parameters'])
        db.execute('''INSERT INTO advances(advance_id,period_key,run_id,source_sha,as_of,basis,status,amount,currency,created_at,body)
            VALUES(?,?,?,?,?,?,'ACTIVE',?,'CNY',?,?)''',
            (advance_id,doc['period_key'],doc['run_id'],doc['source_sha'],doc['as_of'],doc['basis'],
             doc['amount'],_now(),json.dumps(doc,ensure_ascii=False)))
        for line in doc['lines']:
            db.execute('''INSERT INTO advance_lines(advance_id,employee,shift_id,shift_day,basis,open_shift,minutes,amount,evidence)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (advance_id,line['employee'],line['shift_id'],line['shift_day'],line['basis'],
                 1 if line['open_shift'] else 0,line['minutes'],line['amount'],
                 json.dumps(line['evidence'],ensure_ascii=False)))
        db.execute("INSERT INTO settlement_events(settlement_id,advance_id,event,detail,at) VALUES(NULL,?,'ADVANCE_CREATED',?,?)",
            (advance_id,json.dumps({'amount':doc['amount'],'lines':len(doc['lines'])},ensure_ascii=False),_now()))
        return advance_id,True

def get_advance_doc(advance_id):
    a=find_advance(advance_id)
    doc=json.loads(a['body']);doc['status']=a['status'];doc['superseded_by']=a['superseded_by'];doc['settled_by']=a['settled_by']
    doc['created_at']=a['created_at']
    doc['lines']=advance_lines(advance_id)
    return doc

# ---------- settlements ----------

def _money(value):
    return str(Decimal(str(value)).quantize(Decimal('0.01')))

def list_settlements(period_key=None):
    with connection() as db:
        if period_key:
            rows=db.execute('SELECT settlement_id,period_key,version,status,source_sha,run_id,final_amount,advance_total,clawback,net_payable,balanced,sealed,created_at FROM settlements WHERE period_key=? ORDER BY version',(period_key,)).fetchall()
        else:
            rows=db.execute('SELECT settlement_id,period_key,version,status,source_sha,run_id,final_amount,advance_total,clawback,net_payable,balanced,sealed,created_at FROM settlements ORDER BY version').fetchall()
    return [dict(r) for r in rows]

def find_settlement(settlement_id):
    with connection() as db:
        r=db.execute('SELECT * FROM settlements WHERE settlement_id=?',(settlement_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','结清单不存在',404)
    return dict(r)

def get_settlement_doc(settlement_id):
    s=find_settlement(settlement_id)
    doc=json.loads(s['body'])
    with connection() as db:
        doc['advances']=[dict(r) for r in db.execute('SELECT * FROM settlement_advances WHERE settlement_id=? ORDER BY rowid',(settlement_id,)).fetchall()]
        doc['lines']=[dict(r) for r in db.execute('SELECT * FROM settlement_lines WHERE settlement_id=? ORDER BY rowid',(settlement_id,)).fetchall()]
        for item in doc['lines']:
            item['advance_ids']=json.loads(item['advance_ids'])
            item['evidence']=json.loads(item['evidence'])
        doc['events']=[dict(r) for r in db.execute('SELECT event,detail,at FROM settlement_events WHERE settlement_id=? ORDER BY id',(settlement_id,)).fetchall()]
    doc['status']=s['status'];doc['sealed']=bool(s['sealed']);doc['balanced']=bool(s['balanced']);doc['version']=s['version']
    return doc

def current_settlement(period_key):
    with connection() as db:
        r=db.execute("SELECT settlement_id FROM settlements WHERE period_key=? AND status='CURRENT' ORDER BY version DESC LIMIT 1",(period_key,)).fetchall()
    return get_settlement_doc(r[0]['settlement_id']) if r else None

def create_settlement(doc):
    """Create a new settlement version atomically.

    Same source hash + same advance set -> idempotent reuse, no new version.
    Any failure rolls the whole transaction back; the previous CURRENT version stays intact.
    """
    period=doc['period_key']
    db=connection()
    try:
        db.execute('BEGIN IMMEDIATE')
        same=db.execute('SELECT settlement_id,version FROM settlements WHERE period_key=? AND source_sha=? AND status=? ORDER BY version DESC LIMIT 1',
            (period,doc['source_sha'],'CURRENT')).fetchone()
        if same:
            linked={r['advance_id'] for r in db.execute('SELECT advance_id FROM settlement_advances WHERE settlement_id=?',(same['settlement_id'],)).fetchall()}
            wanted={a['advance_id'] for a in doc['advances']}
            if linked==wanted:
                db.execute("INSERT INTO settlement_events(settlement_id,advance_id,event,detail,at) VALUES(?,NULL,'SETTLEMENT_REUSE','重跑同一期间，复用未新增',?)",
                    (same['settlement_id'],_now()))
                db.commit()
                return same['settlement_id'],False,same['version']
        prev=db.execute('SELECT MAX(version) AS v FROM settlements WHERE period_key=?',(period,)).fetchone()
        version=(prev['v'] or 0)+1
        settlement_id=doc['settlement_id']
        save_snapshot(db,doc['source_sha'],doc['source_b64'],doc['parameters'])
        db.execute("UPDATE settlements SET status='SUPERSEDED' WHERE period_key=? AND status='CURRENT'",(period,))
        db.execute('''INSERT INTO settlements(settlement_id,period_key,version,status,source_sha,run_id,
            final_amount,advance_total,clawback,net_payable,balanced,sealed,created_at,body)
            VALUES(?,?,?,'CURRENT',?,?,?,?,?,?,?,0,?,?)''',
            (settlement_id,period,version,doc['source_sha'],doc['run_id'],
             _money(doc['final_amount']),_money(doc['advance_total']),_money(doc['clawback']),
             _money(doc['net_payable']),1 if doc['balanced'] else 0,
             _now(),json.dumps(doc,ensure_ascii=False)))
        for a in doc['advances']:
            db.execute('INSERT INTO settlement_advances(settlement_id,advance_id,advance_status,amount) VALUES(?,?,?,?)',
                (settlement_id,a['advance_id'],a['advance_status'],_money(a['amount'])))
        for line in doc['lines']:
            db.execute('''INSERT INTO settlement_lines(settlement_id,employee,shift_id,final_amount,advance_amount,
                clawback,net_payable,advance_ids,evidence) VALUES(?,?,?,?,?,?,?,?,?)''',
                (settlement_id,line['employee'],line['shift_id'],_money(line['final_amount']),
                 _money(line['advance_amount']),_money(line['clawback']),_money(line['net_payable']),
                 json.dumps(line['advance_ids'],ensure_ascii=False),json.dumps(line['evidence'],ensure_ascii=False)))
        # 补卡/审批变化后旧预支失效：按来源哈希判定是否被本次结清取代
        for a in doc['advances']:
            if a['advance_status']=='SUPERSEDED':
                cur=db.execute('UPDATE advances SET status=?,superseded_by=? WHERE advance_id=? AND status=?',
                    ('SUPERSEDED',settlement_id,a['advance_id'],'ACTIVE')).rowcount
                if cur==0:
                    # 已被更早版本置为 SUPERSEDED：保留首次取代关系，不重复改写、不报错
                    row=db.execute('SELECT status FROM advances WHERE advance_id=?',(a['advance_id'],)).fetchone()
                    if not row or row['status']!='SUPERSEDED':
                        raise DomainError('CONSTRAINT','旧预支状态已变化，拒绝重算')
            else:
                # SETTLED 预支随最新结清重新指向；已 SUPERSEDED 的预支冻结首次取代关系
                cur=db.execute('UPDATE advances SET status=?,settled_by=? WHERE advance_id=? AND status<>?',
                    ('SETTLED',settlement_id,a['advance_id'],'SUPERSEDED')).rowcount
                if cur==0:
                    row=db.execute('SELECT status FROM advances WHERE advance_id=?',(a['advance_id'],)).fetchone()
                    if not row or row['status']!='SUPERSEDED':
                        raise DomainError('CONSTRAINT','预支状态已变化，拒绝重算')
        db.execute("INSERT INTO settlement_events(settlement_id,advance_id,event,detail,at) VALUES(?,NULL,'SETTLEMENT_CREATED',?,?)",
            (settlement_id,json.dumps({'version':version,'balanced':doc['balanced'],'net_payable':_money(doc['net_payable'])},ensure_ascii=False),_now()))
        db.commit()
        return settlement_id,True,version
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def mark_sealed(settlement_id):
    with connection() as db:
        row=db.execute('SELECT balanced,sealed FROM settlements WHERE settlement_id=?',(settlement_id,)).fetchone()
        if not row:raise DomainError('NOT_FOUND','结清单不存在',404)
        if not row['balanced']:raise DomainError('UNBALANCED','结清未对平，不得封存',409)
        if row['sealed']:return False
        db.execute('UPDATE settlements SET sealed=1 WHERE settlement_id=?',(settlement_id,))
        db.execute("INSERT INTO settlement_events(settlement_id,advance_id,event,detail,at) VALUES(?,NULL,'SEALED','对平后封存',?)",
            (settlement_id,_now()))
        return True

def get_snapshot(source_sha):
    with connection() as db:
        r=db.execute('SELECT * FROM source_snapshots WHERE source_sha=?',(source_sha,)).fetchone()
    if not r:return None
    return {'source_sha':r['source_sha'],'source_b64':r['source_b64'],'parameters':json.loads(r['parameters']),'saved_at':r['saved_at']}

def advance_identity(period_key, run_id, as_of, basis):
    raw='|'.join((period_key,run_id,as_of,basis)).encode()
    return 'ADV-'+hashlib.sha256(raw).hexdigest()[:16]

def settlement_identity(period_key, source_sha, advance_ids):
    raw=(period_key+'|'+source_sha+'|'+','.join(sorted(advance_ids))).encode()
    return 'SET-'+hashlib.sha256(raw).hexdigest()[:16]
