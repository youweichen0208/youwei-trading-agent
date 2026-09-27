# gVisor 与 Python 量化栈核实

核实日期：2026-09-27。方法：gvisor.dev 官方文档（install / compatibility）。目标环境 sg-prod：Ubuntu 24.04、kernel 6.8、Docker 29.5.2、未安装 runsc。未做目标机实测。

## 已确认（官方）

| 项目 | 事实 | 来源 |
| --- | --- | --- |
| 平台与内核 | 支持 x86_64 与 ARM64；要求 **Linux 5.6+**（sg-prod kernel 6.8 满足） | [install](https://gvisor.dev/docs/user_guide/install/) |
| 安装方式 | 推荐 apt 仓库（deb 包，可随版本更新）；备选手动下载最新 release | 同上 |
| 兼容性目标 | 官方明示 "support the featureset necessary to be able to run Docker in gVisor, **but not necessarily further**" | [compatibility](https://gvisor.dev/docs/user_guide/compatibility/) |
| io_uring | 默认禁用；启用后仅限基本 IO 操作 | 同上 |
| iptables | 仅部分支持 | 同上 |
| 沙箱内 KVM | 不支持 | 同上 |
| 硬件设备文件 | 一般不支持（NVIDIA GPU / TPU 为例外） | 同上 |

官方兼容性声明没有对 mmap / pyarrow / numpy 给出逐项承诺；Python 数据栈行为属于“必须目标机实测”的部分。注意：pyarrow `read_table()` 默认 `memory_map=False`（普通 IO），mmap 需显式开启（[Arrow 文档](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.read_table.html)）——基准须分别测试两种模式。

## 双模式实测（sg-prod，2026-09-27）

5M 行 / 59MB Parquet，各模式取 3 次最优（镜像 s01-quant-bench，pyarrow 25.0.1）：

| 读取方式 | runc | runsc |
| --- | --- | --- |
| `read_table(memory_map=True)` | 0.25s | 0.28s |
| `read_table(memory_map=False)` | 0.25s | 0.32s |
| `pd.read_parquet`（默认） | 0.37s | 0.37s |

两种 IO 模式在 runsc 下均正常工作，开销在噪声范围。详见 [实测记录](s01-target-verification.md)。

## 待实测（sg-prod + runsc）

1. ~~pyarrow 读写 Parquet（mmap 路径）~~ **已完成**：双模式实测正常，见上表
2. numpy（SIMD/AVX）与 pandas 计算的开销基准（首轮基准已示噪声级开销，可再扩大规模复核）
3. cgroup v2 资源限额（CPU / memory / PID / IO）在 runsc 下是否正常生效（锁定配置首轮已生效，可补 IO 限额专项）
4. `--network=none` 与 runsc 的组合行为（已验证网络阻断，见实测记录）
5. 更大规模快照（多文件、GB 级）的批量读写吞吐

## S03 验收基准建议

在固定合成快照上建立 runc vs runsc 对照：Parquet 读写吞吐、典型 pandas 聚合耗时、内存峰值、进程数。预设可接受阈值（例如 runsc 总耗时 ≤ 2× runc），超阈值时评估：普通 IO 模式 pyarrow、降低快照规模、或将重计算留批处理层。与实施计划 S03 "尚未有生产数据时先用固定合成快照验收" 衔接。
