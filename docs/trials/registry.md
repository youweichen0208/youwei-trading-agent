# Trial Registry

registry_version：1\
创建日期：2026-09-27\
状态：登记载体已建立；目前没有模型比较试验或 release 批准事件。

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

## 3. 记录

目前为空。没有执行过模型比较，不生成示例 trial、虚构 timestamp 或批准记录。
