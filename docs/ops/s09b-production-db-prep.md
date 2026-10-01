# S09b 生产库准备：候选导入与 tenant/计划固定

日期：2026-10-02。环境：sg-prod（DigitalOcean droplet，Docker 29.5.2，4 vCPU / 7.8 GB）。

本记录覆盖 S09b 的「正式库初始化 + 冻结候选导入 + 正式 tenant 固定 + campaign_plan_sha256/scope_manifest 生成」。范围限定为**数据库准备**：本轮不启动生产 API/Worker、不启用自动采集、不创建批准或 Campaign。生产服务的正式发布（启动 API/Worker）仍待新镜像发布校验（GHCR 凭证恢复后按 digest 拉取验证）。

## 一、正式库初始化

- 独立生产 PostgreSQL：`postgres:16-alpine@sha256:721873c3...`，独立持久卷 `youwei-production_postgres_data`，`youwei-production` Compose project。
- 账号分离：`youwei_migrate`（DDL，跑 Alembic）+ `youwei_app`（DML，应用连接）+ `youwei_owner`（initdb 引导）。
- 迁移用已验收本地新镜像 `ghcr.io/youweichen0208/youwei-core:phase1a-s09b`（image ID `sha256:55678d52...`）一次性容器执行 `alembic upgrade head` → 33 表，`alembic_version`=`f9a0b1c2d3e4`。
- 生产 Compose 的 core-api/core-worker image 仍为 registry digest `sha256:7292c853...`（S09a 发布），本轮不启动；GHCR 恢复后按新镜像 digest 更新。

## 二、冻结候选导入（hash 复核通过）

用 `ops/s09b_import_candidate.py`（**纯导入工具**，读取已冻结 JSON、逐项校验后幂等登记；不重跑 `build_s06_release.py`，不重读源码重建 manifest）导入：

- **panel bundle**：`restore_registration_bundle` 校验内容 hash `e36a875c...` 通过，恢复 503 历史成员 + 20 selected + 源/raw 对象，`created=True`。
- **benchmark SPY**：冻结 UUID `ee161699-08b6-4250-986a-b39a393a68df` + ticker identity（valid_from 2026-10-01）。
- **日历**：`xnys-2016-2028-nyse-rules-v2`，3391 交易日。
- **training manifest**：`tm-logistic-ridge-candidate-20261001-v1`，sha256 `febd1080...`（与冻结一致）。
- **release**：`release-logistic-ridge-candidate-20261002-v1`，content sha256 **`bdb8bbe034551d7bcc413965925dc9d7c6f52fa31d4344b8b0c199672c5b4994`**（与 S06i 记录一致）。

导入前校验全部通过：

- release.training_manifest_sha256 == training_manifest content hash ✅
- release.panel_bundle_sha256 == panel bundle content_sha256 ✅
- release.code_files（5 文件）== 当前源码字节 hash ✅（quant/logistic.py、quant/dataset.py、model_registry.py、pipeline.py、uv.lock 均匹配；本轮改动的 api/main.py 不在 code_files 内）
- release.protocol_refs（4 文件）== 当前协议文件 hash ✅
- release.references == 冻结 references.json ✅

幂等性：重跑导入，panel/tm/release 全部 `created=False`，hash 不变。

导入后库状态：securities 504、panel_registrations 1、research_releases 1、training_manifests 1、calendar_days 3391、**release_approvals 0、campaigns 0**、price_observations 0。

## 三、正式 tenant 固定

`ops/s09b_create_tenant.py`（按 slug 幂等，先查复用后创建）：

- slug：`youwei-internal-research`
- **UUID：`f497c122-45b6-497b-bb99-9c42401c3e5f`**（已保存到 `/opt/youwei/production/tenant-uuid.txt`）

## 四、campaign_plan_sha256 与 scope_manifest

`ops/s09b_make_plan.py`（纯计算，从冻结输入重算 hash，不写库）。冻结输入（S06j）：

- campaign_key `phase1a-pilot-2026q4`、release `bdb8bbe0...`、phase `1a`、enabled_sources baseline+quant_model、fallback `phase1a-none`、primary_metric `paired_brier_quant_minus_baseline`、panel 20 只（s00-registration.v2.json）、benchmark SPY `ee161699...`、target D1/D20/D60、time-protocol-v1、first_cutoff `2026-10-10T06:00:00-04:00`、batch_count 12。

结果：

- **planned_cutoffs**（12 批）：2026-10-10 至 2026-12-26；批 1–4 `10:00:00Z`（EDT）、批 5–12 `11:00:00Z`（EST，11-07 DST 切换起）——与 S06j 记录逐项一致。
- **planned_cutoffs_sha256**：`59f7aa3e1303c75da6d1795f5353628ec8dc9e54b78fcd48edbce12a3b290788`
- **campaign_plan_sha256**：`d403c8e553e32e554cc4b587e7f08772f253aced70d2896062a5984aab7b1d29`
- **scope_manifest**：
  ```json
  {
    "schema_version": 1,
    "kind": "campaign_execution",
    "phase": "1a",
    "tenant_id": "f497c122-45b6-497b-bb99-9c42401c3e5f",
    "campaign_key": "phase1a-pilot-2026q4",
    "release_content_sha256": "bdb8bbe034551d7bcc413965925dc9d7c6f52fa31d4344b8b0c199672c5b4994",
    "campaign_plan_sha256": "d403c8e553e32e554cc4b587e7f08772f253aced70d2896062a5984aab7b1d29"
  }
  ```
- **scope_manifest_sha256**：`0b899b003c923efca10dc7de63f4921c1e87ad5223f610caa4ee602689a11805`

产物写入 `/opt/youwei/production/campaign-plan.json`。

## 五、关键注意项（供批准包与正式注册）

- `primary_metric` 的正式值是 **`paired_brier_quant_minus_baseline`**（S06j / s00-registration.v2.json `evaluation.primary_metric`）。`register_campaign` 的默认参数是 `d20_paired_brier_delta`（仅测试占位），**正式注册时必须显式传 `paired_brier_quant_minus_baseline`**，否则 `campaign_plan_sha256` 不匹配、批准 scope 绑定会失败。
- 批准与 Campaign 均未发生：release_approvals 0、campaigns 0、formal_campaign_allowed=false。登记候选、采集行情、生成计划 hash 均不等于批准 release。
- 正式 tenant UUID 与计划 hash 依赖当前 release 内容 `bdb8bbe0...`；若 GHCR 发布后 code_files 再次变化导致重新生成 release，则计划 hash 需按新 release hash 重算。

## 六、脚本与部署修正

本轮交付/修正的脚本（均在 `ops/`）：

- `s09b_deploy.py`：部署驱动（init/migrate/up/down/status）。修正：init 只操作选定环境 + 已有配置差异时报错不覆盖（②）；失败日志经 `_redact` 脱敏命令与输出中的密码/凭证（③）；迁移/导入用本地新镜像；compose env 合并真实供应商凭证。
- `s09b_import_candidate.py`：纯导入工具（①，替代 build_s06_release.py 的导入用途），逐项校验 + 幂等登记。
- `s09b_create_tenant.py`：按 slug 幂等创建/复用正式 tenant。
- `s09b_make_plan.py`：生成 campaign_plan_sha256 + scope_manifest。
- `s09b_acceptance.py` / `s09b_real_collect.py` / `s09b_mock_tiingo.py`：隔离验收（见 [s09b-acceptance.md](s09b-acceptance.md)）。

## 七、剩余

- GHCR 凭证恢复后：推送已验收镜像 `55678d52...`（tag `phase1a-s09b`）→ 取得 registry digest → 更新生产 Compose / upstreams.lock.yaml / deployment-manifest → 按 digest 拉取验证 → 启动正式 API/Worker。
- 正式采集启用：collect_release_id=`release-logistic-ridge-candidate-20261002-v1`、collect_tenant_id=`f497c122-45b6-497b-bb99-9c42401c3e5f`，导入与引用校验完成后启用。
- S09c 运维验收（远端恢复、重启恢复、资源测量、告警、RPO/RTO）。
- 组装批准包（release hash + 计划 hash + 使用范围 + 验收证据）交项目所有者一次性确认。
