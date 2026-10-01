# Trial Registry

registry_version：1\
创建日期：2026-09-27\
状态：Trial 001 已完成规格固定、started及completed追加事件；负向D20结果已保留，无release批准事件。

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

### trial-001 规格固定（运行前追加）

```json
{
  "trial_id": "trial-001-quant-lr-vs-baseline",
  "event_id": "evt-trial-001-spec-fixed",
  "event_type": "corrected",
  "recorded_at": "2026-10-01T08:06:24.326774+00:00",
  "actor_principal_id": "codex_agent",
  "corrects_event_id": "evt-trial-001-registered",
  "spec_ref": "docs/trials/trial-001-spec.v1.json",
  "spec_sha256": "42b58b986a231720d8bf74df1d5541dab5407b48d4e47f1bb435a2001d5e6264",
  "reason": "Fix all candidate parameters and comparison schedule before fetching or inspecting price outcomes; original owner-labelled record remains historical, this action is by agent",
  "prev_event_hash": "cb05a9c736a0a1b7912d6345ea955f854097ac5894862495b27be7b16b637242",
  "result_ref": null,
  "event_hash": "00712bf8d307212e71e6386b574d73a5819f342a3d977ae8f0ced825fca0fe61"
}
```

### evt-trial-001-started-20261001T081321Z

```json
{
  "trial_id": "trial-001-quant-lr-vs-baseline",
  "actor_principal_id": "codex_agent",
  "event_id": "evt-trial-001-started-20261001T081321Z",
  "event_type": "started",
  "recorded_at": "2026-10-01T08:13:21.961055+00:00",
  "input_sha256": "4a0e1492b64b17ed309f07397e3a639196a9b1fdfb2f0be1602ed14a76f5da53",
  "spec_sha256": "42b58b986a231720d8bf74df1d5541dab5407b48d4e47f1bb435a2001d5e6264",
  "code_files": {
    "quant/dataset.py": "3c6a4241e8f854ca01ca66281eabd7e12380a67c057b4a872794358f141fd2e7",
    "quant/logistic.py": "620e42b337f291281480c237de7611a8788aec3a9bb2f8c3a4866953f9ad8b85",
    "quant/trial.py": "57c7b258a346e7ef4aba82dcb02a50fec0739dbd7747d784f49a9d36c6e08b9f",
    "ops/run_s06_trial.py": "edfc21d87037232179b33891f0133ae8d18b750c5ee8fa38a26681b57840ef00",
    "uv.lock": "91565a06acdce3d19173b04c2be67c52b7382d56c02ec914f8c8a170aa7a8993"
  },
  "code_commit": "e017a95abb7aa4e2cf3dab2b1eaac6864f57ee19",
  "code_state": "working_tree_files_fixed_by_content_hash",
  "started_at": "2026-10-01T08:13:21.961055+00:00",
  "spec_ref": "docs/trials/trial-001-spec.v1.json",
  "result_ref": null,
  "prev_event_hash": "00712bf8d307212e71e6386b574d73a5819f342a3d977ae8f0ced825fca0fe61",
  "event_hash": "1e1f1b2d6757229ef82682678f0a8ae6d29ec61de631225e656150f535e47f84"
}
```

### evt-trial-001-completed-20261001T081321Z

```json
{
  "trial_id": "trial-001-quant-lr-vs-baseline",
  "actor_principal_id": "codex_agent",
  "event_id": "evt-trial-001-completed-20261001T081321Z",
  "event_type": "completed",
  "recorded_at": "2026-10-01T08:13:26.545928+00:00",
  "result_ref": ".local/s06-candidate-20261001/trial-001/report.json",
  "result_sha256": "464b56862d7961311d362fc4c198f09e493acfa7a49a07d9be8bb4601680d3de",
  "scope": "retrospective_current_panel_not_formal_pit",
  "primary_test_result": {
    "planned": 2080,
    "paired_scored": 1820,
    "coverage": 0.875,
    "missing_reasons": {
      "partition_embargo": 260
    },
    "baseline_brier": 0.25,
    "quant_brier": 0.25301217828224476,
    "paired_brier_difference": 0.0030121782822447518,
    "calibration_bins": [
      {
        "bin": 0,
        "n": 0,
        "mean_probability": null,
        "observed_rate": null
      },
      {
        "bin": 1,
        "n": 0,
        "mean_probability": null,
        "observed_rate": null
      },
      {
        "bin": 2,
        "n": 0,
        "mean_probability": null,
        "observed_rate": null
      },
      {
        "bin": 3,
        "n": 3,
        "mean_probability": 0.35719468268220966,
        "observed_rate": 0
      },
      {
        "bin": 4,
        "n": 905,
        "mean_probability": 0.4815452656034152,
        "observed_rate": 0.47955801104972373
      },
      {
        "bin": 5,
        "n": 903,
        "mean_probability": 0.5201933588179133,
        "observed_rate": 0.3953488372093023
      },
      {
        "bin": 6,
        "n": 9,
        "mean_probability": 0.6332492199375229,
        "observed_rate": 0.5555555555555556
      },
      {
        "bin": 7,
        "n": 0,
        "mean_probability": null,
        "observed_rate": null
      },
      {
        "bin": 8,
        "n": 0,
        "mean_probability": null,
        "observed_rate": null
      },
      {
        "bin": 9,
        "n": 0,
        "mean_probability": null,
        "observed_rate": null
      }
    ],
    "uncertainty": "descriptive_only",
    "quant_return_mae": 0.059336045887331795,
    "baseline_return_mae": 0.05863483072769231
  },
  "prev_event_hash": "1e1f1b2d6757229ef82682678f0a8ae6d29ec61de631225e656150f535e47f84",
  "event_hash": "2ec859581f0b09b12f59dea6a19587cc25c982dab36a2899eb6538a482da652e"
}
```
