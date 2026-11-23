from flask import Blueprint,jsonify
from app.web import api,page
from app.errors import DomainError
from app import advance_store
from app.workflow import run

bp=Blueprint('m8_advance',__name__)

@bp.get('/m8_advance')
def screen():return page(8,'m8_advance.html')

@bp.route('/api/m8_advance',methods=['GET','POST'])
def preview():return api(8)

@bp.post('/api/advances')
def create_advance():
    # 落库型预支：同源/同 as_of 重跑复用，不新增
    from app.web import payload
    content,parameters=payload()
    state=run(content,parameters,8)
    from app.modules.m8_advance.service import execute_issue
    state=execute_issue(state)
    return jsonify({'advance_id':state['advance']['advance_id'],
                    'created':state['advance']['created'],
                    'status':state['advance']['status'],
                    'summary':state['advance']['summary'],
                    'lines':state['advance']['lines']})

@bp.get('/api/advances')
def list_all():
    from flask import request
    return jsonify(advance_store.list_advances(request.args.get('period_key')))

@bp.get('/api/advances/<advance_id>')
def detail(advance_id):
    return jsonify(advance_store.get_advance_doc(advance_id))
