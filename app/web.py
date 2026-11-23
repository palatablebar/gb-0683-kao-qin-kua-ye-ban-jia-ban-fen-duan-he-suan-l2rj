import base64
from flask import request,render_template,jsonify
from app.errors import DomainError
from app.config import TITLE,MODULES,SHORT
from app.workflow import run,load
from app import store,advance_store

def payload():
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
    if 'csv' in data and not isinstance(data['csv'],str):raise DomainError('CSV','CSV内容必须是文本')
    sample=data.get('sample',request.args.get('sample','dirty.csv'))
    content=data.get('csv')
    return (content.encode() if isinstance(content,str) else load(sample)),data.get('parameters',{})

def api(stage):
    content,parameters=payload();return jsonify(run(content,parameters,stage))

def page(stage,template):
    latest=store.history();state=store.get(latest[0]['id']) if latest else run(load('dirty.csv'),{})
    source=load(request.args['sample']) if 'sample' in request.args else base64.b64decode(state['source_b64']) if state.get('source_b64') else load('dirty.csv')
    parameters=state.get('manifest',{}).get('parameters',{})
    data=run(source,parameters,stage)
    period=str(parameters.get('period_key') or 'PERIOD')
    # 页面可能从没有 period_key 的考勤批次进入；回退到最近有落库记录的期间
    if period=='PERIOD':
        any_settlement=advance_store.list_settlements()
        if any_settlement:period=any_settlement[-1]['period_key']
        else:
            any_advance=advance_store.list_advances()
            if any_advance:period=any_advance[-1]['period_key']
    current_settlement=advance_store.current_settlement(period)
    ledger={'advances':advance_store.list_advances(period),
            'settlements':advance_store.list_settlements(period),
            'current_settlement':current_settlement,
            'all_advances':advance_store.list_advances(),
            'all_settlements':advance_store.list_settlements()}
    return render_template(template,title=TITLE,short=SHORT,modules=MODULES,current=stage,
                           data=data,run=state,history=latest,ledger=ledger)
