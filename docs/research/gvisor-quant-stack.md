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

官方兼容性声明没有对 mmap / pyarrow / numpy 给出逐项承诺；Python 数据栈行为属于"必须目标机实测"的部分。

## 待实测（sg-prod + runsc）

1. **pyarrow 读写 Parquet**（默认 memory-mapped 路径）在 runsc 下的行为与性能；退化为普通 IO 的开关与代价
2. numpy（SIMD/AVX）与 pandas 计算的开销基准
3. cgroup v2 资源限额（CPU / memory / PID / IO）在 runsc 下是否正常生效
4. `--network=none` 与 runsc 的组合行为
5. 文件 IO 密集场景（批量读写快照 Parquet）的吞吐开销

## S03 验收基准建议

在固定合成快照上建立 runc vs runsc 对照：Parquet 读写吞吐、典型 pandas 聚合耗时、内存峰值、进程数。预设可接受阈值（例如 runsc 总耗时 ≤ 2× runc），超阈值时评估：普通 IO 模式 pyarrow、降低快照规模、或将重计算留批处理层。与实施计划 S03 "尚未有生产数据时先用固定合成快照验收" 衔接。
