# Trial Registry

registry_version：1\
创建日期：2026-09-27\
状态：登记载体已建立；已预登记 1 条 `registered` 试验（trial-001，结果留空，无 release 批准事件）。

## 1. 追加规则

本文件的试验记录按追加方式维护。已有记录发现错误时追加 correction 事件并引用原事件；禁止通过改写过去的参数、结果或时间来修饰试验历史。

Git 提供版本与相对已有 commit 的变更证据，不提供绝对不可篡改性。Git 自身支持历史及 reflog 的删除/过期操作，因此仅初始化本地仓库不足以形成独立审计证据。[Git 官方说明](https://git-scm.com/docs/git-reflog)

正式运行前增加受控写入、保护分支/禁止强推、基于可信前序版本的“旧记录前缀未改写”校验，并将登记快照及链头锚定至独立保留存储。当前没有配置远程仓库、CI 或 WORM，不声称这些保护已经存在。

后续迁入 PostgreSQL trials/trial_events 时保留 trial_id、事件链与历史文件引用，确定唯一写入权威；不能把 Markdown 与数据库变成两套可独立修改的事实来源。

## 2. 事件字段

```text
event_id
trial_id
event_type: registered | started | observed | completed | failed | abandoned | corrected
recorded_at
actor_principal_id
hypothesis_family_id
hypothesis
candidate_release_ref
protocol_refs_and_hashes
data_intervals / snapshot_refs
primary_metric / horizon / comparison_sources
parameters / random_seeds
evaluation_schedule / stopping_rule
code_commit
result_ref / failure_reason
corrects_event_id
prev_event_hash / event_hash
```

registered 在第一次查看该试验结果前写入；started 和结果事件沿用同一 trial_id。重复试验、更改特征、参数搜索和中途放弃不能只登记表现最好的一个。

event_hash 对规范化事件内容计算，排除自身 event_hash；第一事件 prev_event_hash 为空。哈希链只有相对于可信外部链头才提供防重写证据。

## 3. 规范化约定（event_hash）

每条记录的 `event_hash` 对规范化事件内容计算：排除 `event_hash` 与 `prev_event_hash` 自身，其余字段按字典序键序、紧凑分隔（`,`/`:`）、`ensure_ascii=False` 的 UTF-8 JSON 序列化后取 SHA-256（与 contracts `_canonical` 一致）。`prev_event_hash` 为空串表示首事件。此 hash 仅对「相对可信外部链头」才提供防重写证据；本地 Git 不提供绝对不可篡改性（见第 1 节）。

## 4. 记录

### trial-001-quant-lr-vs-baseline

```json
{
  "trial_id": "trial-001-quant-lr-vs-baseline",
  "event_id": "evt-trial-001-registered",
  "event_type": "registered",
  "recorded_at": "2026-10-01T01:05:33Z",
  "actor_principal_id": "human_project_owner",
  "hypothesis_family_id": "quant-vs-baseline",
  "hypothesis": "L2-regularized logistic regression (few price-derived features) predicting D20 excess_return>0 improves paired Brier over the constant p=0.5 baseline; calibration quality is itself under test, not assumed",
  "candidate_release_ref": null,
  "protocol_refs_and_hashes": {
    "campaign-policy-v2": "245fbde10fc90f9fa381ecaf9637d5a1889f149988ba2680fe8d2692c5322ec4"
  },
  "data_intervals_snapshot_refs": null,
  "primary_metric": "paired_brier_quant_minus_baseline",
  "horizon": "D20 primary; D1/D60 exploratory",
  "comparison_sources": ["quant_model", "baseline"],
  "parameters": {
    "model_family": "logistic_regression_l2",
    "features": "to_be_fixed_before_started (candidate: momentum, realized volatility; final set frozen at started)",
    "regularization": "to_be_fixed_before_started",
    "train_valid_test_split": "purge_and_embargo_required",
    "label": "d20_excess_return_gt_0_after_maturation",
    "expected_return_model": "none_in_phase_1a_or_separate_model; not derived from win probability"
  },
  "random_seeds": null,
  "evaluation_schedule_stopping_rule": "to_be_registered_before_started (sample size, look count)",
  "code_commit": null,
  "result_ref": null,
  "failure_reason": null,
  "corrects_event_id": null,
  "prev_event_hash": null,
  "event_hash": "cb05a9c736a0a1b7912d6345ea955f854097ac5894862495b27be7b16b637242"
}
```

> 状态说明：本条是 `registered`（试验计划预登记），非结果、非 release 批准。`features`/`regularization`/`random_seeds`/`evaluation_schedule_stopping_rule`/`code_commit` 均标注 `to_be_fixed_before_started` 或 `null`——这些在实验真正开始前以 `started` 事件固定；结果以 `observed`/`completed` 事件追加，失败或放弃如实登记。当前 `trial_count=1`，无 `started`/结果/批准事件。
