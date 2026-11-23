# 考勤跨夜班与加班分段核算

面向制造企业薪酬核算员，处理采信、判定、核算、复核与归档。

## 启动

```bash
docker compose up --build -d
```

访问 http://localhost:18252 。初次启动自动运行脏样例，可切换 clean.csv 与 legacy.csv。顶部“运行批次”会重新计算，同源输入复用批次。

## 业务模块

- 打卡采信：`/m1_ingest`、`/api/m1_ingest`
- 班次日历：`/m2_mapping`、`/api/m2_mapping`
- 跨夜配对：`/m3_versions`、`/api/m3_versions`
- 缺卡复核：`/m4_segments`、`/api/m4_segments`
- 工时切段：`/m5_calculate`、`/api/m5_calculate`
- 薪资对账：`/m6_reconcile`、`/api/m6_reconcile`
- 周期归档：`/m7_archive`、`/api/m7_archive`
- 加班预支：`/m8_advance`、`/api/m8_advance`（落库 `POST /api/advances`）
- 预支结清：`/m9_settlement`、`/api/m9_settlement`（落库 `POST /api/settlements`）

## 预支与结清裁决

月中先按已发生片段发夜班加班预支，月底用完整跨夜打卡与审批结清：

- 预支只采信已闭合事实：整班进出齐全且在 `as_of` 前下班按 `CLOSED` 全额；后半夜未闭合时只对 `as_of` 前已完成计薪片段按 `PARTIAL` 预付，绝不按整班排班预付；无已完成片段不生成预支。
- 结清单逐 员工×班次 对应四项：最终应付、预支已付（含所有实际发放版本）、待扣回 `max(预付−应付,0)`、本次实付 `应付−预付`，恒等式 `最终应付+待扣回=预支已付+本次实付`。
- 同源被更新预支取代的旧单标记 `SUPERSEDED` 并冻结首次取代关系；补卡/审批变化产生新结清版本，旧版本转 `SUPERSEDED` 但全部记录与来源快照保留可追溯。
- 同口径重跑预支与结清均幂等复用，不新增单据、不重复结清；结清生成在单事务内完成，失败整体回滚，旧版保持 `CURRENT`。
- 五项对平核查全部通过才允许封存与导出；页面与导出读取同一份落库结清单。原始打卡字节与参数按来源哈希存入 `source_snapshots`，不随重算丢失。

## 核心规则

跨夜按班次归属日配对，休息区间先扣除，再按法定日期和工时阈值切分倍率。完整数值、边界和异常定义见 `docs/DATA-DICTIONARY.md`。

## 验证

```bash
docker compose exec -T app pytest -q
docker compose config --quiet
```

原始样例在 `sample_data/`。测试在 `tests/`。封存文件是单份CSV，含运行清单、计算证据、隔离原因与对账。复核需提交当前版本，陈旧复核返回409。

## 数据与限制

SQLite保存在records命名卷，普通重启与构建不会清除。确认可丢弃本项目数据后，运行 `docker compose down -v` 再启动可重置。单文件上限2MB；不接入外部业务系统；历史规则由本项目样例定义，不代表实际行业标准。
