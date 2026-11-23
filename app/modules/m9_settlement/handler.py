from flask import Blueprint,jsonify,Response
from app.web import api,page
from app.errors import DomainError
from app import advance_store
from app.modules.m9_settlement.export_settlement import settlement_csv

bp=Blueprint('m9_settlement',__name__)

@bp.get('/m9_settlement')
def screen():return page(9,'m9_settlement.html')

@bp.route('/api/m9_settlement',methods=['GET','POST'])
def preview():return api(9)

@bp.post('/api/settlements')
def create_settlement():
    from app.web import payload
    content,parameters=payload()
    from app.workflow import run
    from app.modules.m9_settlement.service import execute_settle
    state=execute_settle(run(content,parameters,9))
    s=state['settlement']
    return jsonify({'settlement_id':s['settlement_id'],'created':s['created'],'version':s['version'],
                    'balanced':s['balanced'],'sealed':s['sealed'],'status':s['status'],
                    'final_amount':s['final_amount'],'advance_total':s['advance_total'],
                    'clawback':s['clawback'],'net_payable':s['net_payable']})

@bp.get('/api/settlements')
def list_all():
    from flask import request
    return jsonify(advance_store.list_settlements(request.args.get('period_key')))

@bp.get('/api/settlements/<settlement_id>')
def detail(settlement_id):
    return jsonify(advance_store.get_settlement_doc(settlement_id))

@bp.post('/api/settlements/<settlement_id>/seal')
def seal(settlement_id):
    sealed=advance_store.mark_sealed(settlement_id)
    return jsonify({'settlement_id':settlement_id,'sealed':True,'changed':sealed})

@bp.get('/api/settlements/<settlement_id>/export.csv')
def export_csv(settlement_id):
    doc=advance_store.get_settlement_doc(settlement_id)
    # 页面与导出共同呈现同版结清结果；未对平不得封存，也不得导出
    if not doc.get('balanced'):raise DomainError('UNBALANCED','结清未对平，不得导出或封存',409)
    return Response(settlement_csv(doc),mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename=settlement-%s.csv'%settlement_id})
