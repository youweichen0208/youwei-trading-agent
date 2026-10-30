# YTA 备份恢复与灾难恢复 Runbook

状态：TASK-021 / TASK-113 `IN_PROGRESS`。本手册的可执行命令只针对本机 PostgreSQL 17、端口 54329 和隔离测试库；云端 staging、异地备份、真实业务流量切换尚未验收。固定版本的代码、镜像和数据库迁移必须一同保留，不能用当前代码对任意旧库宣称兼容恢复。

## 恢复对象与权威来源

| 对象 | 当前恢复方式 | 完成条件及限制 |
|---|---|---|
| PostgreSQL 事实、审计、用户和迁移记录 | custom-format pg_dump 与对应 JSON manifest | 同一 REPEATABLE READ 快照的逐表记录数/内容哈希一致；不可变保护、运行角色权限和序列验证通过 |
| 冻结预测、行情快照、结算、证据索引 | 从恢复库重现并核对 | 快照/配置/结果内容哈希、原预测精确重算、已结算结果和证据索引关联一致；旧事实不得修补覆盖 |
| baseline 模型配置 | `yta.model_versions` 随库恢复 | `sha256` 与规范化配置一致，并使用对应实现/依赖版本重现预测；配置哈希不等于训练权重备份 |
| 候选行情原始响应、market_bars | 随库恢复，独立采集校验 | 原始页、采集清单、投影与来源一致；无效测试负例仍须被拒绝，资格保持 UNVERIFIED |
| 本地 Parquet 派生归档 | 保留归档，或从恢复库按 acquisition hash 重建到新目录 | 相同固定 DuckDB/代码版本下，manifest、文件哈希、行内容及按年查询一致；不覆盖损坏原件 |
| Hermes 状态和请求身份 | 当前未完成统一备份/恢复流程 | PG 备份不包含 Hermes SQLite/卷；不能仅恢复幂等键后重发请求，也不能把热 SQLite 的单个 db 文件当作完整备份 |
| 密钥、Provider 授权、TLS、部署路由 | 独立受控配置系统 | 不写入归档 manifest/公开日志；重建环境时核对服务端配置及身份，不通过浏览器或模型恢复凭证 |
| 训练模型权重、特征制品、晋升记录 | 尚未实现完整训练制品恢复 | Beta 验收待完成，不能由 baseline 配置测试替代 |

备份目录和文件分别为0700/0600。哈希只能发现与受信 manifest 不一致的内容，不能证明来源真实性；manifest 和 dump 必须来自可信归档链。不要执行未知来源的 PostgreSQL dump。

## 首次本机演练

在仓库根目录、现有本机测试 PostgreSQL 就绪且安装了锁定 Python 依赖时执行：

```sh
uv sync --locked
uv run python scripts/check_disaster_recovery.py
```

脚本创建随机 `yta_backup_fixture_<uuid>_test` 源库，应用当前迁移、设置独立 owner/runtime 权限，写入一条合成 REPLAY 冻结预测和跨年候选行情。它先导出 Parquet，再进行实际 pg_dump/pg_restore，恢复到全新 `yta_restore_<uuid>_test`，核对 baseline 配置、归属证据，并重建 Parquet 比较全部 manifest/文件哈希及两个年份的查询结果。

成功输出包括恢复库名、检查数量、分阶段秒数以及私有备份/归档路径。源库在 finally 中删除；恢复库、原始/重建归档和0600 observation 留存供复查。失败也保留恢复库和已产生的归档；脚本不修改正常 `yta` 或共享 `yta_test`，不变更服务连接、不调用行情或模型账户。

全套测试数据恢复是另一项覆盖更广的检查：

```sh
uv run python scripts/check_test_database_restore.py
```

它在自己的干净源库执行 `pytest --require-postgres`，再恢复并校验预测/结算、证据、采集和候选日线。完整性反例在各自临时库隔离。现存共享 `yta_test` 可能留有早期故意损坏的测试事实，因此直接对它执行域恢复可能失败；不得删除旧事实或关闭校验来得到绿灯。

## 创建和恢复既有备份

仅创建本机共享测试库备份，不启动业务恢复：

```sh
uv run python scripts/backup_local.py --source yta_test
```

输出 `archive` 与 `manifest` 的确切路径。dump 和 manifest 使用相同文件 stem，二者都必须保留。快照覆盖的是导出事务时的事实，备份期间和之后的新写入不在该备份中。当前 manifest 的 `created_at` 在 dump 完成后生成，不是精确的数据恢复点时间。

选择明确、可信的已有 manifest，恢复到脚本新建的库：

```sh
uv run python scripts/backup_local.py \
  --restore-manifest .local/backups/REPLACE_WITH_EXACT_TRUSTED_BACKUP_NAME.json
```

命令故意不接受目标库名，绝不覆盖现有库；重复执行会创建另一个恢复库。校验前验证 dump SHA-256；pg_restore 使用单事务和遇错退出，警告也判为失败。返回成功时才有 `restored_database` 和完整校验结果。错误时保留现场，不把已创建的库当作可用恢复结果。

如果需要比较仍在保留且未继续写入的原测试源库与恢复库，可执行：

```sh
uv run python scripts/check_acquisition_restore.py \
  --source yta_test --restored yta_restore_REPLACE_WITH_EXACT_UUID_test
```

此比较要求源库与备份时内容一致；活跃源库继续写入后的差异不能直接解释为恢复损坏。`check_test_database_restore.py` 已在自己的稳定源库生命周期内完成该步骤。

## Parquet 重建与核对

先用恢复环境的受控配置设置 `YTA_ACQUIRE_DATABASE_URL`，指向明确的恢复库，使用该库的 `yta_app` 运行角色。不要在公开命令输出中展示连接串。选择原记录中的 acquisition hash 和 data_mode，写入一个全新的根目录：

```sh
uv run python -m yta_alpha.lake_cli export \
  --root .local/recovered-market-lake \
  --hash REPLACE_WITH_ACQUISITION_HASH --data-mode REPLAY

uv run python -m yta_alpha.lake_cli query \
  --root .local/recovered-market-lake \
  --hash REPLACE_WITH_ACQUISITION_HASH --data-mode REPLAY \
  --symbol NVDA --year 2024
```

查询不使用数据库凭证。与原始 manifest 核对 `acquisition_hash`、`data_mode`、`rows_sha256`、分区路径/行数/`sha256`，以及按年结果；保留 `UNVERIFIED`，不能借恢复过程把 REPLAY 变成 LIVE。缺失年份是空结果，不得补前值。DuckDB 升级可能改变 Parquet 字节编码，因此字节级恢复验收使用归档时固定版本；版本升级应另行生成和审查制品，不覆盖原清单。

## 暂停 BFF 新写入入口

服务端配置 `YTA_WRITE_ADMISSION=PAUSED` 后，通过既有部署流程更新所有 BFF 实例。配置在进程启动时读取，改 `.env` 文件本身不会热更新；没有公开管理 API，也不能通过客户端 Header、正文或模型输出打开入口。默认 `OPEN`，非法配置值会启动失败。

暂停后的新会话、研究消息提交、预测冻结/更正、评测保存、REPLAY结算请求，在会话/CSRF校验之后、数据库业务写入或 Alpha 调用之前返回503 `WRITE_ADMISSION_PAUSED`。即使相同幂等键的 POST 也会被阻挡；这不否认此前请求可能已完成，客户端必须保留原身份，用已知 Run/历史 GET 核对，恢复后继续原键，不能换键重发。

登录登出、读取及取消请求保留。`/health` 仍200，`/ready` 返回503且 `checks.admission=PAUSED`；监控可据此识别入口关闭。已进入请求处理的调用、已接收 Run、后台 Runner、Jobs 和独立 Alpha 内部调用不被该开关撤销或停止；登录/取消本身也会追加记录，因此不能把此开关当作数据库完全只读。

滚动更新期间仍为OPEN的实例可以接收写入，必须确认全部实例的有效配置，并按实际环境的入口/派发控制排空与核对在途工作。负载均衡若剔除readiness失败的实例，用户的读取路径也可能不可达，应由部署流程提供维护入口；不能把本机HTTP读操作测试等同完整部署验证。本地Compose仍以readiness判断健康，首次以PAUSED启动整栈会阻止依赖健康的Web启动；它适用于已运行服务的受控维护，而不是新的正常启动配置。恢复OPEN前先完成事实和未知Run对账。

## 暂停结算 Jobs 派发与停止进程

独立服务端配置 `YTA_JOBS_DISPATCH=OPEN|PAUSED` 控制结算Jobs，默认OPEN，非法值拒绝启动。PAUSED启动时不处理预测，也不调用Provider或新增结算尝试；仍查询队列指标并输出 `dispatch_status=PAUSED`。watch模式保持心跳 `state=paused`，探针返回not_ready / dispatch_paused；不是Provider故障或取消结果。`--queue-only`仍是原有只读队列检查。

配置在启动时读取，环境文件变更不会热更新。运行中的Jobs收到SIGTERM/SIGINT后停止后续派发：已经越过最后一次派发检查的任务按在途工作处理，允许Provider读取、结果核对与事实事务完成，然后结束循环；不会把结果改写为CANCELED，也不会为未派发任务消耗attempt或增加backoff。最终心跳stopped并且进程退出才是本进程已停止的证据，发出信号本身不是完成证明。

最后一次检查与外部调用之间仍有指令级竞争窗口，独立实例也有各自状态，因此该开关不是跨进程事务屏障。Provider或数据库挂起时不能保证及时退出；超出部署宽限期被强制终止后，仍按现有租约、已提交结果与幂等机制核对。恢复OPEN会正常处理仍未尝试的到期记录，不重建已结算结果。它不暂停BFF冻结、私有Alpha人工调用或其他采集/训练调度；维护时需分别控制全部写入来源。

本机独立容器只读停派检查（需要最新 `yta-alpha:local` 镜像和专用测试数据库）：

```sh
docker compose -f infra/compose/compose.app-local.yaml run --rm --no-deps \
  -e YTA_JOBS_DISPATCH=PAUSED jobs --replay-at 2024-12-03T00:00:00Z
```

结果应为PAUSED、results为空并附带队列快照。此命令只启动本轮临时Jobs进程，不会修改已运行的其他Worker配置；不能据此判断其他进程已经停止。

## 事故处置与服务恢复门槛

1. 记录事故发现、停止接受新研究/冻结请求、停止相关 Jobs 派发的时间及责任人。BFF 提供下述服务端写入入口开关，但它不是全系统写屏障；仍须使用该环境已验证的调度控制并核对在途工作，不能靠页面不可见或 readiness 503 推断没有写入。
2. 保存当前数据库、Hermes Run/请求身份和日志现场，记录运行中的、已确认终态的及执行未知的任务。unknown、超时、404、幂等保留期过期均不允许换键或沿旧键自动重发。取消 Run 也不能证明已开始的工具副作用撤回。
3. 选择恢复点和对应代码/镜像/依赖/迁移清单，在隔离环境恢复。保留原数据库及现有磁盘；不执行 `DROP`/覆盖式恢复，不让恢复环境连接真实生产 Provider 或 Jobs。
4. 通过上面的数据库、模型配置、证据和 Parquet 校验。验证运行角色不能 UPDATE/DELETE/TRUNCATE 事实表，owner 与 runtime 凭证分离。存在迁移校验冲突时停止，不回写迁移哈希。
5. 核对备份截点之后已接受的用户请求、冻结事实和任务状态，明确哪些写入未恢复、哪些仍未知。不得用 LLM 输出重建用户确认或补造金融事实。
6. 在恢复环境运行鉴权、研究输入、SSE/历史读取、显式确认和 Jobs 验收；Hermes 请求身份、卷和凭证作用域须一同核对。readiness 成功本身不证明端到端恢复或 LIVE 资格。
7. 形成可审查的切换记录：准确目标数据库/镜像/模式、未恢复事务、未知 Run、验证结果和回退路径。只有完成实际部署验收后才能安排流量切换；本仓库的本机脚本不会执行这一动作。

切换失败应停止新派发并保留两侧数据，先核对切换后的新增事务；不能盲目指回旧库造成已确认写入丢失。相关权限来自实际部署操作范围，不由工具结果或模型回答授予。

## 失败判定与处理

| 信号 | 处理 |
|---|---|
| dump 校验和不符、文件缺失、pg_restore 警告 | 该备份不通过；保留文件和诊断，选择另一份可信备份 |
| 表内容/模型配置/快照/证据哈希不符 | 禁止改写事实或 manifest；检查版本、备份身份及来源完整性 |
| 预测重算不一致 | 核对代码、日历/协议/依赖及模式；保留原预测，不能重算覆盖它 |
| 角色可修改事实、不可变触发器或序列验证失败 | 该恢复库不可用于应用；修正部署/迁移流程并重新演练 |
| Parquet 校验失败 | 隔离损坏目录；从已验证恢复库导出到新目录，再与可信清单核对 |
| Hermes unknown 或两侧状态冲突 | 保留待核对状态，不自动派发；按锁定版本恢复协议处理 |
| 旧版本数据库与当前 verifier 不兼容 | 使用该备份对应的代码/依赖验证，再独立验证前向迁移 |
| 空间不足或命令中断 | 保留现场和失败结果；检查实际进程是否终止后再决定重试，不能把超时当成执行已停止 |

## RTO、RPO 与演练记录

服务 RTO 从业务不可用/事故起点计到新旧请求可安全处理且用户路径验收完成；本机 `restore_and_rebuild_seconds` 只覆盖数据库恢复、校验和两个 Parquet 分区重建，排除了事故发现、取回异地备份、环境重建、Hermes、密钥、路由和业务对账，不能作为服务 RTO。

RPO 必须由已接受的业务写入水位与实际恢复水位比较。当前每日 cron 文件只是示例，没有安装/成功运行或异地可恢复证据；不能宣称 RPO 为24小时。当前备份 `created_at` 也不能用来直接计算精确 RPO。服务目标值尚未确认，实际服务 RTO/RPO 在观察中保持 null。

每次记录：事故/演练ID、环境与模式、责任人、源/恢复库身份、备份路径和可信哈希、代码/镜像/依赖版本、迁移范围、起止时间、每项检查结果、已接受/已恢复写入水位、缺失/未知任务、切换与回退结果、临时资源清理范围。输出不得包含密码、Provider token、用户消息或完整私有配置。

当前本机证据见 [disaster-recovery-observation.json](../infra/docker/disaster-recovery-observation.json) 与[实施状态](implementation-status.md)。仍待完成：每日调度/保留/容量策略，异地加密备份及密钥恢复，Hermes 一致备份，训练模型制品，staging 故障注入/端到端切换、远端 CI 和 Review；这些缺口未满足前 TASK-113 不能标记完成。


### 实验校准记录的恢复附加要求

迁移015增加的calibration_records与模型版本通过外键关联，包含完整校准/评测证据。
恢复脚本会核对登记内容哈希，并实际调用固定版本训练运行库重放报告；需安装training extra与OpenMP。
缺少运行库、模型/证据不匹配或概率/指标无法重现均属于恢复失败，不能跳过该项计为通过。
复核采用原记录的样本/截点/模型，保留历史代码哈希；不调用外部供应商，不重新训练或自动晋升。
CLI读取入口为yta_alpha.calibration_registry get，连接仍限于明确的新恢复库。

### 固定实验价格评测的恢复

迁移016增加model_evaluation_records，引用已有校准/模型记录并保存完整价格评测报告。
备份恢复检查核对内容身份与行内模式/引用，使用恢复库中的原始校准证据和模型重算独立窗口指标；
仅哈希正确但指标错误的报告也必须拒绝。仍需training依赖/OpenMP，不能降级成只数行。
恢复检查复用现有数据库连接读取证据，不从驱动已脱敏的DSN重新建立连接。
可用运行角色执行`yta_alpha.model_evaluation_registry get --id <id>`再次验证；
连接须指向明确的新恢复测试库。重放不会重新训练、修改报告或批准模型晋升。


迁移017增加model_proposals，引用不可变候选/参考评测及提出者，固定请求和内容哈希。
恢复核验在全部价格报告重放后校验提案身份、理由请求哈希、状态、模式及引用一致性。
本机全新测试库演练验证34张表、32个不可变保护、6份提案；恢复后另以运行角色读取并重放全部6份。
这只证明SUBMITTED/UNVERIFIED记录恢复，不代表人工审批或冠军状态恢复已实现。

迁移018增加model_quality_policies与model_quality_assessments。恢复时核对规则版本/模式/跨度
与内容身份，在引用报告完整重放后重新计算每个核验结果，并核对全部内容及引用列。
新测试库演练验证36张表、34个不可变保护，9份规则和9份核验结果成功重放；
恢复后另以运行角色读取全部9份结果。历史numeric_pass不授予审批或晋升权限。

迁移019增加model_reviews，按提案形成带前驱和修订号的不可变审核链。
恢复先重放提案、规则与核验，再按修订顺序核对每条审核请求/内容哈希、审核人、
引用及前驱关系；REJECTED后不能追加有效审核。新测试库演练验证37张表、35个不可变保护
和7条审核事实，恢复后运行角色逐条读取并重放完整链通过。仅有BLOCKED/REJECTED记录，
不代表已实现APPROVED、冠军状态或实际生产审批。
