"""结清导出：页面与导出共用同一份结清单版本，未对平禁止导出/封存。"""
import io,csv,json

def settlement_csv(doc):
    out=io.StringIO(newline='');writer=csv.writer(out)
    rid=doc.get('settlement_id','preview')
    writer.writerow(['记录类型','结清单','版本','员工/班次','字段','内容'])
    writer.writerow(['结清抬头',rid,doc.get('version',''),'', '',
                     json.dumps({k:doc.get(k) for k in
                                 ('period_key','source_sha','run_id','final_amount','advance_total',
                                  'clawback','net_payable','balanced','status','sealed')},
                                ensure_ascii=False,sort_keys=True)])
    for check in doc.get('checks',[]):
        writer.writerow(['对平核查',rid,doc.get('version',''),'',check['code'],
                         json.dumps(check,ensure_ascii=False,sort_keys=True)])
    for adv in doc.get('advances',[]):
        writer.writerow(['预支依据',rid,doc.get('version',''),'',adv['advance_id'],
                         json.dumps(adv,ensure_ascii=False,sort_keys=True)])
    for line in doc.get('lines',[]):
        writer.writerow(['结清明细',rid,doc.get('version',''),
                         line['employee']+'/'+line['shift_id'],
                         'final={f} advance={a} clawback={c} net={n}'.format(
                             f=line['final_amount'],a=line['advance_amount'],
                             c=line['clawback'],n=line['net_payable']),
                         json.dumps(line,ensure_ascii=False,sort_keys=True)])
    for emp in doc.get('employees',[]):
        writer.writerow(['员工汇总',rid,doc.get('version',''),emp['employee'],'',
                         json.dumps(emp,ensure_ascii=False,sort_keys=True)])
    for event in doc.get('events',[]):
        writer.writerow(['事件留痕',rid,doc.get('version',''),'',event['event'],
                         json.dumps(event,ensure_ascii=False,sort_keys=True)])
    return '﻿'+out.getvalue()
