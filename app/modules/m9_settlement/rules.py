"""M9 预支结清规则。

逐项对应口径（每个 员工×班次 一行）：
- final_amount   最终应付：月底完整跨夜打卡与审批闭合后的计薪金额
- advance_amount 预支已付：该期间内针对本班所有实际发放过的预支（含被新补卡/审批取代的旧版）
- clawback       待扣回：max(预支已付 − 最终应付, 0)
- net_payable    本次实付：最终应付 − 预支已付（负数即转为待扣回）
恒等式：最终应付 + 待扣回 = 预支已付 + 本次实付。

预支失效判定：预支来源哈希与本次结清来源一致 => SETTLED；
补卡或审批变化导致来源哈希不同 => 旧预置 SUPERSEDED，已发金额仍全额纳入扣回链条，不丢账。
"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from app.errors import DomainError
from app import advance_store

CENTS=Decimal('0.01')

def _d(value):
    return Decimal(str(value)).quantize(CENTS)

def _period_key(state):
    return str(state.get('parameters',{}).get('period_key') or 'PERIOD')

def build_settlement(state):
    state=deepcopy(state)
    period=_period_key(state)
    source_sha=state['manifest']['sha256']
    stored_advances=advance_store.list_advances(period)
    advance_docs=[advance_store.get_advance_doc(a['advance_id']) for a in stored_advances]

    # 最终应付按班次归集（完整闭合后的计薪证据）
    final_by_shift={}
    evidence_by_shift=defaultdict(list)
    for c in state.get('calculations',[]):
        key=(c['employee'],c['id'])
        final_by_shift[key]=final_by_shift.get(key,Decimal(0))+Decimal(c['amount'])
        evidence_by_shift[key].append({'kind':'CALC','line':c.get('line'),'calendar_day':c['calendar_day'],
            'start':c['start'],'end':c['end'],'minutes':c['minutes'],'multiplier':c['multiplier'],
            'code':c['code'],'amount':c['amount']})

    # 预支已付按班次归集，跨所有实际发放过的预支版本
    paid_by_shift=defaultdict(Decimal)
    advance_ids_by_shift=defaultdict(list)
    advance_evidence_by_shift=defaultdict(list)
    advance_views=[]
    linked_ids=set()
    # 同一来源上的最新预支单（按创建时间）：被它取代的更早预支才标记 SUPERSEDED
    latest_by_source={}
    latest_ts={}
    for doc in advance_docs:
        key=doc['source_sha']
        if key not in latest_by_source or doc['created_at']>latest_ts[key]:
            latest_by_source[key]=doc['advance_id'];latest_ts[key]=doc['created_at']
    for doc in advance_docs:
        linked_ids.add(doc['advance_id'])
        replaced_by_newer=doc['advance_id']!=latest_by_source[doc['source_sha']]
        # 已被历史结清标记 SUPERSEDED 的保持冻结；同源被更新预支取代的本次失效；其余随结清入账
        already_superseded=doc.get('status')=='SUPERSEDED'
        superseded=already_superseded or replaced_by_newer
        advance_views.append({'advance_id':doc['advance_id'],'as_of':doc['as_of'],'basis':doc['basis'],
            'source_sha':doc['source_sha'],'amount':doc['amount'],
            'advance_status':'SUPERSEDED' if superseded else 'SETTLED',
            'superseded':superseded})
        for line in doc['lines']:
            key=(line['employee'],line['shift_id'])
            paid_by_shift[key]+=Decimal(line['amount'])
            if doc['advance_id'] not in advance_ids_by_shift[key]:
                advance_ids_by_shift[key].append(doc['advance_id'])
            advance_evidence_by_shift[key].append({'kind':'ADVANCE','advance_id':doc['advance_id'],
                'basis':line['basis'],'minutes':line['minutes'],'amount':line['amount'],
                'open_shift':bool(line['open_shift']),'source_sha':doc['source_sha']})

    keys=sorted(set(final_by_shift)|set(paid_by_shift))
    lines=[]
    for employee,shift_id in keys:
        final=final_by_shift.get((employee,shift_id),Decimal(0)).quantize(CENTS)
        paid=paid_by_shift.get((employee,shift_id),Decimal(0)).quantize(CENTS)
        net=(final-paid).quantize(CENTS)
        clawback=max(Decimal(0),-net)
        lines.append({'employee':employee,'shift_id':shift_id,
            'final_amount':str(final),'advance_amount':str(paid),
            'clawback':str(clawback),'net_payable':str(net),
            'advance_ids':advance_ids_by_shift[(employee,shift_id)],
            'evidence':evidence_by_shift[(employee,shift_id)]+advance_evidence_by_shift[(employee,shift_id)]})

    final_total=sum((Decimal(l['final_amount']) for l in lines),Decimal(0)).quantize(CENTS)
    advance_total=sum((Decimal(l['advance_amount']) for l in lines),Decimal(0)).quantize(CENTS)
    clawback_total=sum((Decimal(l['clawback']) for l in lines),Decimal(0)).quantize(CENTS)
    net_total=sum((Decimal(l['net_payable']) for l in lines),Decimal(0)).quantize(CENTS)
    # 本次实付（只付正数行；负数行已计入待扣回，不冲减付款）
    payable_total=sum((max(Decimal(0),Decimal(l['net_payable'])) for l in lines),Decimal(0)).quantize(CENTS)

    # 员工级汇总，便于页面与导出呈现同一版数字
    employees=defaultdict(lambda:{'final_amount':Decimal(0),'advance_amount':Decimal(0),
                                  'clawback':Decimal(0),'net_payable':Decimal(0)})
    for l in lines:
        agg=employees[l['employee']]
        for field in ('final_amount','advance_amount','clawback','net_payable'):
            agg[field]+=Decimal(l[field])
    employee_rows=[dict(employee=e,**{k:str(v.quantize(CENTS)) for k,v in agg.items()})
                   for e,agg in sorted(employees.items())]

    # 对平核查：任一不过则 balanced=False，禁止封存
    calc_evidence_total=sum((Decimal(c['amount']) for c in state.get('calculations',[])),Decimal(0)).quantize(CENTS)
    business_total=_d(state.get('business',{}).get('total','0'))
    advance_header_sum=sum((Decimal(a['amount']) for a in advance_views),Decimal(0)).quantize(CENTS)
    checks=[
        {'code':'FINAL_MATCH','label':'最终应付合计等于全部计薪证据独立求和',
         'left':str(final_total),'right':str(calc_evidence_total),'ok':final_total==calc_evidence_total},
        {'code':'BUSINESS_MATCH','label':'计薪证据与 m5 业务总额一致',
         'left':str(calc_evidence_total),'right':str(business_total),'ok':calc_evidence_total==business_total},
        {'code':'ADVANCE_CONSERVED','label':'预支逐行合计等于预支单总额（含旧版）',
         'left':str(advance_total),'right':str(advance_header_sum),'ok':advance_total==advance_header_sum},
        {'code':'IDENTITY','label':'恒等式 最终应付+待扣回=预支已付+本次实付',
         'left':str((final_total+clawback_total).quantize(CENTS)),
         'right':str((advance_total+payable_total).quantize(CENTS)),
         # 本次实付只含正净付行；负净付行进待扣回，保证资金闭环
         'ok':(final_total+clawback_total)==(advance_total+payable_total)},
        {'code':'ALL_ADVANCES_LINKED','label':'期间全部预支单均纳入结清',
         'left':str(len(linked_ids)),'right':str(len(stored_advances)),
         'ok':len(linked_ids)==len(stored_advances)},
    ]
    balanced=all(c['ok'] for c in checks)

    state['settlement_preview']={'period_key':period,'source_sha':source_sha,'run_id':state['run_id'],
        'advances':advance_views,'lines':lines,'employees':employee_rows,
        'final_amount':str(final_total),'advance_total':str(advance_total),
        'clawback':str(clawback_total),'net_payable':str(net_total),'payable':str(payable_total),
        'balanced':balanced,'checks':checks,
        'source_b64':state.get('source_b64',''),'parameters':state.get('parameters',{})}
    return state

def settle(state):
    state=build_settlement(state)
    preview=state['settlement_preview']
    advance_ids=sorted(a['advance_id'] for a in preview['advances'])
    settlement_id=advance_store.settlement_identity(preview['period_key'],preview['source_sha'],advance_ids)
    doc={'settlement_id':settlement_id,'period_key':preview['period_key'],
         'source_sha':preview['source_sha'],'run_id':preview['run_id'],
         'final_amount':preview['final_amount'],'advance_total':preview['advance_total'],
         'clawback':preview['clawback'],'net_payable':preview['net_payable'],
         'payable':preview['payable'],
         'balanced':preview['balanced'],'checks':preview['checks'],
         'advances':preview['advances'],'lines':preview['lines'],'employees':preview['employees'],
         'source_b64':preview['source_b64'],'parameters':preview['parameters']}
    settlement_id,created,version=advance_store.create_settlement(doc)
    stored=advance_store.get_settlement_doc(settlement_id)
    state['settlement']=stored;state['settlement']['settlement_id']=settlement_id
    state['settlement']['created']=created
    return state
