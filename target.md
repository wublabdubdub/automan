# 竞品数据库自动化测试目标

## 最终目标

建设一套面向 Ymatrix 与竞品数据库的自动化测试框架。框架通过统一任务模板驱动测试流程，自动连接目标数据库，执行标准化 benchmark，采集系统资源、数据库状态、perf 信息和测试日志，并在测试结束后生成可复盘、可对比、可追踪的深度报告。

最终系统要解决的问题不是“跑一次测试”，而是形成一条长期可复用的测试闭环：

```text
任务模板 -> 环境检查 -> 数据准备 -> 测试执行 -> 指标采集 -> 结果归档 -> 深度报告
```

## 核心问题

框架最终需要帮助回答以下问题：

1. 在同样数据规模、同样 SQL、同样测试规则下，Ymatrix 和 Doris 的性能差异是什么？
2. 哪些 TPC-H 查询 Ymatrix 更快，哪些 Doris 更快？
3. 性能差异主要来自 CPU、内存、磁盘 IO、网络、执行计划，还是数据库内部机制？
4. 单条 Query 的耗时、资源消耗和 perf 热点是否能对应起来？
5. 本次测试的环境、参数、数据规模、SQL、日志和指标是否完整保存，是否可复现？
6. 基于测试结果，下一轮应该优化数据库参数、调整 SQL、扩大数据规模，还是补充新的测试场景？

## v0.1 目标

v0.1 先实现 Ymatrix 与 Doris 的 TPC-H 自动化测试闭环。

范围包括：

1. 使用 YAML 定义一次测试任务。
2. 支持 Ymatrix 与 Doris 两类数据库连接。
3. 自动检查数据库连通性和基础版本信息。
4. 支持 TPC-H 表结构初始化。
5. 支持 TPC-H 数据装载。
6. 执行 TPC-H 22 条标准查询。
7. 记录每条 Query 的执行耗时、执行状态和错误信息。
8. 采集测试期间的基础系统资源信息。
9. 采集 perf stat 信息。
10. 按 run_id 归档任务配置、原始日志、benchmark 结果、指标数据和报告。
11. 生成 Markdown 格式的测试报告。

## 技术栈

第一阶段采用：

```text
Python + Ansible + YAML + TPC-H SQL + Linux perf + 系统采集工具 + Markdown 报告
```

职责划分：

```text
Python:
  测试主控、任务解析、流程编排、数据库连接、结果解析、报告上下文生成

Ansible:
  远程环境检查、依赖检查、命令执行、日志和文件收集

YAML:
  测试任务模板

Linux perf / pidstat / iostat / vmstat:
  资源与性能指标采集

Agent:
  基于结构化结果和原始日志生成深度分析报告
```

## 已确认测试对象

Ymatrix 连接方式：

```yaml
type: ymatrix
host: 172.16.100.29
port: 5432
database: postgres
user: zhangchen
```

Doris 连接方式：

```yaml
type: doris
host: 172.16.100.10
port: 9030
user: root
```

敏感信息不直接写入目标文档，后续应通过本地配置文件、环境变量或密钥文件注入。

## 已固化契约

当前阶段先固化两项工程契约：

```text
configs/databases/
  数据库模板，当前包含 Ymatrix 与 Doris。

configs/benchmarks/
  Benchmark 模板，当前包含 TPC-H。

configs/collectors/
  采集策略模板，当前包含 default。

configs/tasks/
  实验任务模板，当前包含 Ymatrix vs Doris TPC-H。

docs/config-template-design.md
  配置模板解耦设计，说明 include、overrides 和后续扩展规则。

docs/runs-structure.md
  每次测试运行的 runs/ 结果目录结构，定义归档文件、指标、日志和报告上下文的位置。

docs/tpcc-automan-flow.md
  当前 TPC-C-only 阶段的 task.yaml 模板、automan run --task 和 automan progress 执行流程。
```

## 执行位置约束

当前目录是项目源码目录，只用于编辑代码、配置、文档和模板。

实际测试必须上传到远端机器执行：

```text
host: 172.16.100.143
user: root
remote_path: /root/automan
```

后续 BenchmarkSQL 构建、测试执行、指标采集、perf 采集和 runs/ 产物生成，默认都应在远端 `/root/automan` 下完成。

## 总体执行流程

一次完整测试运行应包含以下阶段：

```text
1. load_task
   读取 task.yaml，校验任务结构。

2. init_run
   生成 run_id，创建结果目录，保存任务快照。

3. check_env
   检查数据库连通性、数据库版本、主机基础信息、测试工具依赖。

4. prepare_schema
   创建 TPC-H 数据库、schema 和表结构。

5. load_data
   装载 TPC-H 数据，记录装载耗时和失败信息。

6. warmup
   执行预热查询，降低冷启动影响。

7. run_benchmark
   执行正式 TPC-H 查询，记录每条 Query 的耗时、状态和输出。

8. collect_metrics
   在测试过程中采集 CPU、内存、磁盘 IO、网络、进程和 perf 信息。

9. archive
   归档配置、日志、SQL、结果、指标和环境信息。

10. report
    生成 Markdown 报告，并为 Agent 深度分析准备结构化上下文。
```

## 结果归档目标

每次测试都必须可复盘。建议每次运行生成独立目录：

```text
runs/
  <run_id>/
    task.yaml
    env/
    sql/
    logs/
    metrics/
    perf/
    benchmark/
    report/
```

需要保存的内容包括：

1. 原始任务模板。
2. 数据库版本和连接目标。
3. 测试机器 CPU、内存、磁盘、网络信息。
4. TPC-H 表结构和查询 SQL。
5. 数据装载日志。
6. 每条 Query 的执行结果和耗时。
7. 系统资源采集结果。
8. perf stat 输出。
9. 测试过程日志。
10. 最终 Markdown 报告。

## 暂不纳入 v0.1 的内容

以下能力暂时不做，避免第一阶段范围过大：

1. Web 管理页面。
2. 多用户权限系统。
3. 复杂任务调度平台。
4. Kubernetes 编排。
5. 自动调参。
6. 长周期稳定性测试。
7. TPC-C。
8. TDengine 时序测试。
9. 复杂可视化大屏。

## 阶段判断标准

v0.1 完成的标志是：

```text
可以通过一份 task.yaml，
自动完成 Ymatrix 与 Doris 的 TPC-H 测试，
归档完整原始数据，
并生成一份能够解释测试过程、结果差异和下一步建议的 Markdown 报告。
```
