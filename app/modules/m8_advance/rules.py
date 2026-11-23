"""M8 加班预支规则。

裁决口径：
- 预支只采信“已闭合事实”：整班有进有出（CLOSED 依据）按完整切段金额预付；
  跨夜班后半夜尚未闭合时，只对截止 as_of 已完成的片段预付（PARTIAL 依据），
  未完成部分不进入预支，绝不按整班排班金额预付。
- 没有任何已完成片段的班次不生成预支行，避免凭空预付。
- 金额全程 Decimal，按分四舍五入；预支逐行留痕（来源行、片段、分钟、倍率）。
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from app.errors import DomainError
from app.modules.m3_versions.rules import ZONE
from app import advance_store

CENTS=Decimal('0.01')

def _dt(value):
    return datetime.fromisoformat(value).astimezone(ZONE)

def _as_of(state):
    raw=state.get('parameters',{}).get('as_of')
    if raw in (None,''):
        return None
    try:
        d=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
        if d.tzinfo is None:raise ValueError()
    except (ValueError,TypeError):
        raise DomainError('PARAMETERS','as_of 必须是带时区的 ISO 时间')
    return d.astimezone(ZONE)

def _period_key(state):
    return str(state.get('parameters',{}).get('period_key') or 'PERIOD')

def _advance_for_shift(shift, calculations, cutoff):
    """Return (line_or_None, open_flag). Partial segments only for open shifts."""
    employee=shift['employee'];shift_id=shift['id']
    items=sorted((c for c in calculations if c['id']==shift_id),key=lambda c:c['start'])
    if not items:
        # 缺卡整班在 m4 已隔离，无任何计薪片段：未闭合且无可预付内容
        return None,True
    if shift['in'] is not None and shift['out'] is not None:
        end=_dt(shift['out']['at'])
        if end<=cutoff:
            amount=sum((Decimal(c['amount']) for c in items),Decimal(0))
            line={'employee':employee,'shift_id':shift_id,'shift_day':shift['day'],'basis':'CLOSED',
                  'open_shift':False,'minutes':sum(int(c['minutes']) for c in items),
                  'amount':str(amount),
                  'evidence':[{'line':c.get('line'),'calendar_day':c['calendar_day'],'start':c['start'],
                               'end':c['end'],'minutes':c['minutes'],'multiplier':c['multiplier'],
                               'code':c['code'],'amount':c['amount']} for c in items]}
            return line,False
        # 有完整打卡但截止时间早于下班：仍按已完成片段预付
    # 未闭合（缺 OUT 或 OUT 在 as_of 之后）：只取 end<=as_of 的已完成计薪片段
    partial=[c for c in items if _dt(c['end'])<=cutoff]
    if not partial:return None,True
    amount=sum((Decimal(c['amount']) for c in partial),Decimal(0))
    line={'employee':employee,'shift_id':shift_id,'shift_day':shift['day'],'basis':'PARTIAL',
          'open_shift':True,'minutes':sum(int(c['minutes']) for c in partial),
          'amount':str(amount),
          'evidence':[{'line':c.get('line'),'calendar_day':c['calendar_day'],'start':c['start'],
                       'end':c['end'],'minutes':c['minutes'],'multiplier':c['multiplier'],
                       'code':c['code'],'amount':c['amount']} for c in partial]}
    return line,True

def build_advance(state):
    state=deepcopy(state)
    cutoff=_as_of(state)
    if cutoff is None:
        # 非预支场景（如月底结清流水线经过本阶段）：不生成预支
        state['advance']={'as_of':None,'period_key':_period_key(state),
                          'basis_rule':'未提供 as_of，跳过预支计算','lines':[],
                          'summary':{'line_count':0,'open_shifts':0,'skipped_open_no_segment':0,
                                     'minutes':0,'amount':'0.00','currency':'CNY'}}
        return state
    lines=[];open_shifts=0;skipped_open=0
    for shift in state['shifts']:
        line,is_open=_advance_for_shift(shift,state['calculations'],cutoff)
        if is_open:open_shifts+=1
        if line is None:
            if is_open:skipped_open+=1
            continue
        lines.append(line)
    total=sum((Decimal(l['amount']) for l in lines),Decimal(0)).quantize(CENTS)
    summary={'line_count':len(lines),'open_shifts':open_shifts,'skipped_open_no_segment':skipped_open,
             'minutes':sum(l['minutes'] for l in lines),'amount':str(total),'currency':'CNY'}
    state['advance']={'as_of':cutoff.isoformat(),'period_key':_period_key(state),
                      'basis_rule':'CLOSED 整班闭合全额；PARTIAL 仅预付 as_of 前已完成片段，不按排班预付',
                      'lines':lines,'summary':summary}
    return state

def issue(state):
    """Persist an advance idempotently keyed by (period, run, as_of, basis)."""
    state=build_advance(state)
    adv=state['advance']
    if adv['as_of'] is None:
        raise DomainError('PARAMETERS','预支必须提供 as_of 截止时间（含时区）')
    advance_id=advance_store.advance_identity(adv['period_key'],state['run_id'],adv['as_of'],adv['basis_rule'])
    doc={'advance_id':advance_id,'period_key':adv['period_key'],'run_id':state['run_id'],
         'source_sha':state['manifest']['sha256'],'as_of':adv['as_of'],'basis':adv['basis_rule'],
         'amount':adv['summary']['amount'],'parameters':state.get('parameters',{}),
         'source_b64':state.get('source_b64',''),'lines':adv['lines']}
    saved_id,created=advance_store.create_advance(doc)
    stored=advance_store.get_advance_doc(saved_id)
    adv['advance_id']=saved_id;adv['created']=created
    adv['status']=stored['status'];adv['stored_lines']=stored['lines']
    adv['superseded_by']=stored.get('superseded_by');adv['settled_by']=stored.get('settled_by')
    return state
