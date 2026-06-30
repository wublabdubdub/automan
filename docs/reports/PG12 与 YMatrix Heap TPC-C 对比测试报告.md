# PG12 与 YMatrix Heap TPC-C 对比测试报告

## 1. 报告概述

本报告基于 `172.16.100.143:/root/automan` 上的 TPC-C 自动化测试结果，对 PostgreSQL 12 heap 单节点目标（下文简称 PG12）与 YMatrix heap master-only 目标（下文简称 YMatrix Heap）进行性能和资源占用对比分析。

本次分析覆盖吞吐结果、并发扩展趋势、CPU/内存/IO 资源占用、perf 采样产物、BenchmarkSQL 标准输出和错误日志。报告重点不只呈现最终 tpmC 数字，也关注测试过程是否稳定、日志是否干净，以及资源消耗与吞吐之间是否匹配。

## 2. 数据来源与测试范围

### 2.1 数据来源

- 执行机：`172.16.100.143`
- 工作目录：`/root/automan`
- Job ID：`20260628-224417-tpcc`
- 结果查看命令：`./automan list -t tpcc`
- 资源采集目录：`/root/automan/runs/<run_id>/collectors/runBenchmark.sh/database/`
- BenchmarkSQL 日志目录：`/root/automan/runs/<run_id>/logs/`

### 2.2 测试对象

| 对象 | Automan Target | 数据库地址 | 端口 | 数据库 | DDL Profile |
| --- | --- | --- | ---: | --- | --- |
| PostgreSQL 12 heap 单节点 | `pg` | `192.168.100.30` | 5232 | `tpcc` | `postgresql_heap_single_node` |
| YMatrix heap master-only | `ymatrix_heap` | `192.168.100.29` | 5432 | `tpcc` | `ymatrix_heap_master_only` |

### 2.3 测试矩阵

| 维度 | 值 |
| --- | --- |
| Benchmark | TPC-C / BenchmarkSQL |
| Warehouses | 100 |
| Terminals | 100, 500, 1000 |
| Run Mins | 15 |
| 系统采样间隔 | 1 秒 |
| 系统采样工具 | `vmstat`, `iostat -x`, `mpstat -P ALL`, `pidstat -durh` |
| perf 采样 | runBenchmark 阶段 3 段采样，每段 60 秒，99 Hz |

### 2.4 实际生效参数

本报告中的参数以测试后登录数据库执行 `pg_settings` 查询得到的实际生效值为准，而不是以 Automan 计划文件中的推荐值为准。查询时间为 2026-06-29，查询来源为 `172.16.100.143` 通过 `psql` 连接两侧数据库。

| 参数 | PG12 实际值 | PG12 来源 | YMatrix Heap 实际值 | YMatrix 来源 |
| --- | ---: | --- | ---: | --- |
| `server_version` | `12.12` | - | `12.12` | - |
| `max_connections` | `1050` | configuration file | `1050` | configuration file |
| `shared_buffers` | `62GB` | configuration file | `64GB` | configuration file |
| `effective_cache_size` | `175GB` | configuration file | `128GB` | configuration file |
| `work_mem` | `61MB` | configuration file | `4MB` | configuration file |
| `maintenance_work_mem` | `12GB` | configuration file | `2GB` | configuration file |
| `max_wal_size` | `64GB` | configuration file | `64GB` | configuration file |
| `min_wal_size` | `8GB` | configuration file | `8GB` | configuration file |
| `checkpoint_timeout` | `30min` | configuration file | 未返回 | - |
| `checkpoint_completion_target` | `0.9` | configuration file | `0.9` | configuration file |
| `vacuum_cost_limit` | `10000` | configuration file | `10000` | configuration file |

两侧参数并不完全相同。PG12 的 `effective_cache_size`、`work_mem`、`maintenance_work_mem` 明显高于 YMatrix Heap；YMatrix Heap 的 `shared_buffers` 略高于 PG12。尤其是 `work_mem` 差异较大，PG12 为 61MB，YMatrix Heap 为 4MB。因此，本轮结果应解释为“当前实际部署参数下的对比”，不应表述为严格等参数对比。

## 3. 测试结果汇总

### 3.1 BenchmarkSQL 吞吐结果

| Terminals | PG12 tpmC | YMatrix Heap tpmC | PG12 相对 YMatrix | PG12 tpmTOTAL | YMatrix Heap tpmTOTAL | 结论 |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 100 | 104,138.55 | 102,325.19 | 1.02x | 235,598.78 | 231,604.27 | 两者接近，PG12 小幅领先 |
| 500 | 104,498.54 | 103,377.64 | 1.01x | 236,238.47 | 234,022.08 | 两者接近，PG12 小幅领先 |
| 1000 | 137,573.89 | 43,284.76 | 3.18x | 311,237.68 | 98,033.39 | PG12 明显领先，YMatrix Heap 吞吐塌陷 |

从最终 tpmC 看，100 和 500 terminals 下两者结果非常接近，PG12 分别领先约 1.77% 和 1.08%。到 1000 terminals 时，PG12 的 tpmC 上升到 137,573.89，而 YMatrix Heap 下降到 43,284.76，PG12 约为 YMatrix Heap 的 3.18 倍。

### 3.2 并发扩展趋势

| 对象 | 100 -> 500 terminals | 500 -> 1000 terminals | 现象 |
| --- | ---: | ---: | --- |
| PG12 | tpmC +0.35% | tpmC +31.65% | 高并发下吞吐继续提升 |
| YMatrix Heap | tpmC +1.03% | tpmC -58.13% | 1000 并发出现明显退化 |

PG12 在 100 和 500 terminals 下基本处于同一吞吐平台，到 1000 terminals 时吞吐继续提升，说明该负载下 PG12 尚能从更高客户端并发中获得收益。YMatrix Heap 在 100 和 500 terminals 下同样接近平台值，但 1000 terminals 时吞吐下降超过一半，说明系统进入了非线性退化区间。

## 4. 资源占用对比

以下资源数据均来自 `runBenchmark.sh` 阶段。CPU 使用率采用 `mpstat all` 统计，Run Queue、内存和部分系统指标来自 `vmstat`，IO 指标来自 `iostat -x` 中物理块设备统计。

### 4.1 CPU 与调度压力

| Terminals | 对象 | CPU 平均 | CPU P95 | CPU 最大 | Run Queue 平均 | Run Queue P95 | Run Queue 最大 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | PG12 | 30.43% | 39.46% | 83.17% | 18.59 | 27.75 | 106 |
| 100 | YMatrix Heap | 41.06% | 74.26% | 93.89% | 25.45 | 42.65 | 100 |
| 500 | PG12 | 35.34% | 53.95% | 100.00% | 23.17 | 41.00 | 472 |
| 500 | YMatrix Heap | 54.10% | 92.27% | 99.92% | 43.81 | 141.20 | 565 |
| 1000 | PG12 | 53.19% | 77.49% | 100.00% | 44.74 | 130.40 | 883 |
| 1000 | YMatrix Heap | 90.01% | 99.26% | 99.69% | 297.08 | 810.00 | 992 |

CPU 维度可以看到三个明显特征：

1. 在 100 terminals 下，YMatrix Heap 的 CPU 平均使用率比 PG12 高约 10.6 个百分点，但 tpmC 略低。
2. 在 500 terminals 下，YMatrix Heap 的 CPU P95 已达到 92.27%，Run Queue P95 达到 141.20，说明调度排队压力已经明显高于 PG12。
3. 在 1000 terminals 下，YMatrix Heap CPU 平均值达到 90.01%，Run Queue 平均值达到 297.08，P95 达到 810.00，系统已处于严重 CPU/调度拥塞状态；PG12 虽然 Run Queue 也升高，但 CPU 平均值仍为 53.19%，吞吐没有塌陷。

从单位吞吐 CPU 成本看，YMatrix Heap 在 1000 terminals 下的 CPU 使用率与 tpmC 明显失衡。按每 1 万 tpmC 消耗的 CPU 百分点粗略估算，PG12 约为 3.87，YMatrix Heap 约为 20.80，说明 YMatrix Heap 在高并发下花费了更多 CPU，但有效事务吞吐大幅下降。

### 4.2 内存与 Swap

| Terminals | 对象 | 可用内存最低值 | Free 最低值 | Swap 最大值 |
| ---: | --- | ---: | ---: | ---: |
| 100 | PG12 | 237.00 GB | 28.90 GB | 0 |
| 100 | YMatrix Heap | 218.77 GB | 103.82 GB | 0 |
| 500 | PG12 | 222.87 GB | 29.08 GB | 0 |
| 500 | YMatrix Heap | 190.49 GB | 84.72 GB | 0 |
| 1000 | PG12 | 203.88 GB | 58.48 GB | 0 |
| 1000 | YMatrix Heap | 168.69 GB | 74.66 GB | 0 |

两组测试均未发生 Swap，内存不是本次吞吐差异的直接瓶颈。YMatrix Heap 随并发提升可用内存下降更明显，但仍保留较大余量。1000 terminals 下的主要压力来自 CPU 与调度排队，而不是内存耗尽。

### 4.3 IO 写入与磁盘利用率

| Terminals | 对象 | 读 MB/s 平均 | 写 MB/s 平均 | 写 MB/s P95 | 写 MB/s 最大 | 磁盘 util 平均 | 磁盘 util P95 | 写等待 P95 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | PG12 | 0.01 | 54.24 | 86.28 | 144.08 | 95.19% | 98.00% | 0.06 ms |
| 100 | YMatrix Heap | 0.01 | 133.47 | 206.50 | 1045.47 | 95.42% | 98.20% | 0.45 ms |
| 500 | PG12 | 0.02 | 75.69 | 191.01 | 568.30 | 91.85% | 96.90% | 0.12 ms |
| 500 | YMatrix Heap | 0.03 | 125.02 | 182.71 | 249.15 | 87.76% | 96.70% | 0.41 ms |
| 1000 | PG12 | 0.00 | 69.76 | 129.40 | 539.88 | 87.07% | 96.37% | 0.20 ms |
| 1000 | YMatrix Heap | 0.06 | 29.49 | 60.05 | 406.67 | 26.82% | 48.21% | 0.49 ms |

IO 指标显示，100 和 500 terminals 下 YMatrix Heap 的写入量明显高于 PG12，但吞吐并未超过 PG12，说明其单位有效事务的写入成本更高。1000 terminals 下 YMatrix Heap 写入量反而下降，磁盘 util 平均值也降到 26.82%，这与吞吐塌陷和 CPU/调度拥塞同时出现，说明此时瓶颈已经不在磁盘写满，而是大量时间消耗在 CPU、调度、异常处理或内部竞争路径上。

PG12 三组测试中磁盘 util 都较高，但写等待 P95 很低，且吞吐随并发提高没有退化，说明磁盘忙碌并没有演化成主要阻塞点。

## 5. 错误日志与结果可信度分析

### 5.1 错误日志统计

| Terminals | PG12 stderr 行数 | PG12 错误匹配 | YMatrix Heap stderr 行数 | YMatrix Heap 错误匹配 | YMatrix Heap 重复键错误出现次数 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 0 | 0 | 1,046,416 | 149,488 | 74,744 |
| 500 | 0 | 0 | 3,244,304 | 463,472 | 231,736 |
| 1000 | 0 | 0 | 1,864,686 | 266,492 | 132,488 |

PG12 三组测试的 `runBenchmark.sh.stderr.log` 均为空。YMatrix Heap 三组测试虽然最终状态为 success，并且 BenchmarkSQL 输出了 Measured tpmC，但 stderr 中存在大量异常堆栈，主要错误为：

```text
org.postgresql.util.PSQLException: ERROR: duplicate key value violates unique constraint "bmsql_oorder_pkey"
```

这些错误意味着 YMatrix Heap 测试过程中存在大量订单主键冲突。由于 TPC-C 的 New Order 事务是 tpmC 的核心指标，重复键错误会直接影响测试语义和结果可信度。当前 Automan 将 runBenchmark 阶段定义为“进程退出 0 且最终 Measured tpmC 存在则保留结果”，因此这些 run 被列为 success；但从正式测试报告角度，应将 YMatrix Heap 的结果标记为“有严重异常，需复核后再作为有效横向对比依据”。

### 5.2 对结果解释的影响

100 和 500 terminals 下，PG12 与 YMatrix Heap 的 tpmC 接近，但 YMatrix Heap 同时伴随大量重复键异常。因此不能简单解读为“两者性能相当”。更严谨的判断是：在当前测试条件和当前日志质量下，PG12 给出了干净、可采信的结果；YMatrix Heap 虽然最终 tpmC 接近 PG12，但测试过程存在大量事务异常，结果需要重新验证。

1000 terminals 下，YMatrix Heap 的吞吐明显下降，同时 CPU 与 Run Queue 激增，并继续存在大量重复键异常。该结果更像是系统在异常和高并发压力下进入退化状态，而不是一个稳定的高并发 TPC-C 性能点。

## 6. perf 采样观察

### 6.1 perf 产物状态

| Terminals | 对象 | perf data 文件 | perf data 大小 | report 文件 | 备注 |
| ---: | --- | ---: | ---: | ---: | --- |
| 100 | PG12 | 4 | 197.14 MB | 3 | 采样完整 |
| 500 | PG12 | 4 | 245.41 MB | 3 | 采样完整 |
| 1000 | PG12 | 4 | 331.57 MB | 3 | 采样完整 |
| 100 | YMatrix Heap | 4 | 243.17 MB | 3 | 采样完整 |
| 500 | YMatrix Heap | 4 | 375.87 MB | 3 | 采样完整 |
| 1000 | YMatrix Heap | 0 | 0 MB | 3 | `perf.data` 被跳过，仅保留 report |

YMatrix Heap 1000 terminals 的 perf data 被标记为 skipped，仅保留了 perf report 文本。这不会影响对 CPU/IO/日志的整体判断，但会限制后续做更深入火焰图或 perf script 级别分析。

### 6.2 perf 热点方向

PG12 的 perf report 顶层热点主要落在 PostgreSQL 的服务端执行路径，例如 `PostmasterMain`、`ServerLoop`、`PostgresMain`、`PortalRun`、`ExecutorRun` 等路径，符合数据库执行 SQL 事务时的典型表现。

YMatrix Heap 在 100 和 500 terminals 下同样可见 PostgreSQL/YMatrix 服务端主循环和执行路径。到 1000 terminals 时，perf report 顶层出现大量 kernel syscall/write 相关路径，例如 `entry_SYSCALL_64_after_hwframe`、`do_syscall_64`、`vfs_write`、`ksys_write`、`new_sync_write`。结合 stderr 中大量异常输出和 CPU/Run Queue 飙升，可以判断 1000 terminals 下 YMatrix Heap 的资源消耗已经明显偏离正常事务执行路径，异常处理和写日志/写输出路径可能放大了系统压力。

## 7. 分项对比结论

### 7.1 吞吐能力

PG12 在三组并发下均高于 YMatrix Heap。100 和 500 terminals 下领先幅度小，但结果干净；1000 terminals 下 PG12 明显领先，且没有错误日志。YMatrix Heap 在 1000 terminals 下 tpmC 下降到 43,284.76，说明当前配置和当前测试形态下无法稳定承载该并发压力。

### 7.2 资源效率

YMatrix Heap 在 100 和 500 terminals 下使用了更高 CPU 和更多写 IO，但没有获得更高 tpmC。1000 terminals 下 CPU 平均值达到 90.01%，但吞吐只有 PG12 的约 31.46%，资源效率显著下降。

PG12 的资源曲线更平滑：并发提升时 CPU 和 Run Queue 上升，但吞吐没有下降；磁盘 util 虽高，但写等待很低，没有表现出明显 IO 等待瓶颈。

### 7.3 稳定性与测试有效性

PG12 三组测试没有 stderr 输出，结果可直接作为本轮有效数据。YMatrix Heap 三组测试全部存在大量重复键异常，尤其 500 terminals 下 stderr 超过 324 万行，重复键错误出现超过 23 万次。即使 Automan 列为 success，也不建议将这些结果作为最终性能结论直接发布，应先定位重复键问题并重跑。

## 8. 风险与限制

1. YMatrix Heap 的重复键异常会破坏 TPC-C 测试语义，当前 YMatrix Heap 结果应标记为“异常条件下的观测值”。
2. 两侧数据库实际生效参数不完全一致，尤其 `work_mem`、`effective_cache_size`、`maintenance_work_mem` 差异较大；本报告反映当前实际部署参数下的观测结果，不构成严格等参数对比。
3. 本报告资源分析聚焦 `runBenchmark.sh` 阶段，不覆盖建表和装载阶段的完整资源消耗。
4. IO 统计来自数据库主机物理块设备层，未进一步拆分 WAL、数据文件、日志输出等具体写入来源。
5. YMatrix Heap 1000 terminals 的 perf data 未保留，后续深入分析需要重新采样。
6. 本次对比是单次结果对比，没有包含多轮重复测试的方差和置信区间。

## 9. 建议

1. 优先排查 YMatrix Heap 的 `bmsql_oorder_pkey` 重复键问题，确认是 BenchmarkSQL 事务逻辑、DDL/profile、sequence 初始化、并发事务隔离，还是 YMatrix master-only 执行路径导致。
2. 在修复重复键异常前，不建议将 YMatrix Heap 当前 tpmC 与 PG12 作为正式性能胜负结论对外发布。
3. 修复后按相同矩阵重跑 100、500、1000 terminals，并要求 `runBenchmark.sh.stderr.log` 无 ERROR/Exception 后再进入正式横向对比。
4. 对 1000 terminals 单独增加一次 YMatrix Heap perf 完整采样，保留 `perf.data`、`perf script` 和 `perf report`，用于确认 CPU 消耗是否仍集中在 syscall/write 或异常相关路径。
5. 若后续目标是评估稳定上限，建议增加 600、700、800、900 terminals 梯度，定位 YMatrix Heap 从平台期到塌陷区间的拐点。

## 10. 总体结论

在本轮 `20260628-224417-tpcc` 测试中，基于两侧数据库实际生效参数，PG12 表现为结果干净、资源曲线较稳定、并发提升后仍能获得更高吞吐；YMatrix Heap 在 100 和 500 terminals 下最终 tpmC 与 PG12 接近，但资源消耗更高，并且存在大量重复键异常。到 1000 terminals 时，YMatrix Heap 出现吞吐塌陷、CPU 接近打满、Run Queue 大幅积压，同时继续伴随大量异常日志。

因此，本轮可确认的正式结论是：在当前实际部署参数与当前测试执行结果下，PG12 整体优于 YMatrix Heap，尤其在 1000 terminals 高并发场景下优势显著。YMatrix Heap 当前结果存在严重异常，必须先解决重复键问题，并在明确参数基线后重跑，才能作为严格意义上的有效 TPC-C 横向对比数据。
