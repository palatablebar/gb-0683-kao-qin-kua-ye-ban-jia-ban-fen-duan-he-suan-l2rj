from app.modules.m8_advance.rules import build_advance,issue

def execute(state):
    # 预览接口只做计算，不落库；落库走 /api/advances
    return build_advance(state)

def execute_issue(state):
    return issue(state)
