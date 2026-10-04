# 三仓库重构验收（2026-10-04）

本轮为源码重构和隔离验证，未部署 VM。此前未提交的拆分及部署工作独立保存于基线提交 `d5b1fba`；个人浏览器运行产物未纳入提交。

## 实现与保留项

| 范围 | 处理 |
| --- | --- |
| Core HTTP | 认证依赖集中，路由按 runs/data/research/campaigns/ops/admin 分组；OpenAPI 与基线完全一致 |
| 探索研究 | 包入口保留公开调用，submission/execution/reports/models 按职责分离；原事务、租约、fencing 和版本语义保持 |
| Runner | app 负责装配，routes 分开 HTTP 面，state 管授权/句柄/生命周期；三处相同 CLI 子进程实现合并到 process；容器权限和网络策略仍各自维护 |
| 研究运行时 | usage 独立记录计数与归因；Hermes 调用、提案解析及最小归因补丁保持 |
| 助手 | policy 维护唯一工具允许列表；bootstrap 共用 profile 安装与发现；注册、处理器分离；无平台或兄弟目录依赖 |
| WebUI | 官方功能与源码不删改；自有工作流验证 PR、覆盖源码变化，只有 youwei 分支可发布，镜像标注 source/revision |
| 旧入口 | 删除 Research Pipe 和其 13 项测试；保留旧 Pipe 停用及聊天兼容逻辑。当前接口已覆盖身份、幂等、取消，助手补充显式取消测试 |
| 发布准备 | 移除 WebUI 验收的隐式旧镜像；候选生成器要求两个 digest，从实际 Compose 仅替换镜像；拒绝旧拓扑、浮动镜像、build 和覆盖文件，输出 0600 |
| 文档 | 旧实施计划、评审和 Pipe 指南归档，当前索引与操作说明更新 |

正式研究绑定的五个代码/锁文件、迁移、协议、Trial 和历史发布文件共 57 个文件与重构前 SHA256 对照一致。
`pipeline` 仍依赖本地子进程适配器，因此保留该兼容链；移除须独立评估 ResearchRelease。S06 构建/登记、S09 部署以及 S12 演练脚本仍承担发布或复现职责，没有按文件年代批量删除。
跨仓 mock 模型有意独立维护，避免重新引入源码依赖。历史聊天 Compose/candidate catalog 与日期化证据不代表当前部署；已部署镜像指针未更新为本轮候选。

## 已执行验证

- 重构前平台完整测试：705 passed。
- Core 受影响切片：42 passed；Runner 契约/容器切片：48 passed。
- 重构后平台完整测试：698 passed，0 skipped（删除 Pipe 13 项，新增配置保护 6 项）。随后配置输入结构保护扩展，候选配置测试单独 10 passed；最终代码的远端完整测试 **702 passed、零跳过**。
- 独立 Python 3.14 研究运行时：本机和远端均 87 passed。
- 助手：24 passed；固定 Hermes 原生 mock 验收 PASS，独立 arm64 镜像构建通过。
- catalog：平台及助手登记均 VALID；Core OpenAPI 基线完全一致；改动模块 F 类静态检查通过。
- 候选配置测试使用合成镜像和临时目录，不读取生产秘密；缺少显式 WebUI 镜像的验证命令如预期拒绝。

- 探索研究 21 个定义的语法树与基线一致；Runner 31 个接口/执行函数在闭包变量替换为实例状态后，归一化语法树一致。
- Core 与 Runner wheel 构建通过，检查包内容确认未混入对方运行包；Compose 模板用合成凭证渲染通过。
- 助手候选镜像无网络启动/重启、新备份按镜像元数据恢复、无元数据备份显式镜像恢复及缺镜像拒绝均 PASS。
- [平台完整 CI](https://github.com/youweichen0208/youwei-trading-agent/actions/runs/37195317245)、[助手 CI](https://github.com/youweichen0208/trading-assistant/actions/runs/37195118697)、[WebUI 完整镜像 CI](https://github.com/youweichen0208/youwei-webui/actions/runs/37195122736) 均通过。

## 跨服务候选验收

WebUI 本机首次构建因 Debian 软件源连接失败中断；重试到模型预下载阶段因 Hugging Face DNS/连接失败退出。完整镜像已在 GitHub CI 构建通过，本机失败不计为通过。平台新增 Chat compatibility 工作流，以明确的助手/WebUI 完整提交独立构建并验证组合，[跨服务 CI](https://github.com/youweichen0208/youwei-trading-agent/actions/runs/37195773177) 已通过：两个仓库从固定提交各自构建，临时 WebUI 用户/聊天、模型发现、完整历史追问、SSE、知识跨聊天、重启、镜像内备份隔离恢复、发现故障下后台分流、关闭注册与原聊天保留全部 PASS。镜像未发布，也未部署 VM。

## 未验证与授权范围

本轮不部署目标机，不运行真实付费模型、不进行真实 EODHD 套餐查询，不执行正式前向评估。性能没有进行专门测量，不宣称吞吐或延迟改善。本机 Docker 测试不等于目标机 gVisor 或生产资源验收。

## 交付

- [平台草稿 PR](https://github.com/youweichen0208/youwei-trading-agent/pull/1)：基线单独提交，随后按 Core、Runner、研究运行时、旧入口与 CI 分片提交。
- [助手草稿 PR](https://github.com/youweichen0208/trading-assistant/pull/1)：当前完整候选提交见 `infra/chat/verification-sources.json`；最终跟进仅清理文件尾空行。
- [WebUI 草稿 PR](https://github.com/youweichen0208/youwei-webui/pull/1)：固定候选提交 `09137c785`；官方功能和应用源码保持，修改仅自有镜像工作流。

本机 WebUI 完整构建的网络失败如上保留；GitHub Linux 临时环境的完整构建与组合验收通过不能改写成本机构建成功。浏览器人工操作、目标机资源、真实模型与正式评估均不在本轮验证范围。
