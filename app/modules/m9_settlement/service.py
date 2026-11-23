from app.modules.m9_settlement.rules import build_settlement,settle

def execute(state):
    # 预览不落库；落库走 /api/settlements
    return build_settlement(state)

def execute_settle(state):
    return settle(state)
