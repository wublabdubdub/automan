# Automan 中文使用手册

本文档说明当前 Pigsty 风格改造后的 Automan 如何使用。当前阶段主要支持 **TPC-C / BenchmarkSQL**，目标数据库包括 PostgreSQL 与 YMatrix 的 heap、mars3 形态。

## 1. 使用原则

Automan 的定位是数据库压测操作台，不是交互式配置工具。

核心原则：

```text
配置模板 -> 校验 -> 生成手工参数命令 -> 用户手工改参数 -> 数据库与采集环境检查 -> 执行压测并采集资源/perf -> 查看进度 -> 生成客观性能报告 -> 必要时清理
```

重要约束：

- 当前源码目录只用于开发、编辑、提交代码。
- 真实压测必须在执行机 `/root/automan` 下运行。
- 数据库参数修改只生成命令，不由 Automan 自动执行。
- Automan 不会自动改 `postgresql.conf`。
- Automan 不会自动执行 `gpconfig`。
- Automan 不会自动重启 PostgreSQL 或 YMatrix。
- 正式压测前，用户必须手工确认参数、连接信息、仓数、并发数。

当前执行机约定：

```text
host: 172.16.100.143
user: root
path: /root/automan
```

## 2. 目录结构

主要目录如下：

```text
automan/
  configure                 从 conf 模板生成 automan.yml
  automan                   Python 主入口
  check.yml                 环境检查 playbook
  tpcc.yml                  TPC-C 正式压测 playbook
  tpcc-rm.yml               显式清理 TPC-C 对象 playbook
  report.yml                生成报告 playbook

  conf/
    tpcc/
      base.yml                 TPC-C 共享矩阵与采集配置
      targets/
        pg.yml                 PostgreSQL 单机 heap 目标片段
        ym-heap.yml            YMatrix heap master only 目标片段
        ym-mars3.yml           YMatrix mars3 master only 目标片段

  playbooks/                Ansible playbook 实现
  roles/                    Ansible roles
  automan_core/             Python 控制面
  benchmarks/               BenchmarkSQL DDL profile
  tools/benchmarksql/       BenchmarkSQL 工具
  runs/                     job 与 run 结果目录
  work/                     每个 run 的 BenchmarkSQL 工作目录
```

## 3. 最短执行路径

在执行机上运行：

```bash
cd /root/automan

./configure -c ym-mars3 -t tpch -o automan.yml
./automan param -i automan.yml
# 手工检查屏幕输出的修改命令和确认命令，并按需执行

./check.yml -i automan.yml
./tpcc.yml -i automan.yml

./automan list
./report.yml -i automan.yml
```

如果只是生成计划，不执行压测：

```bash
./automan run -i automan.yml --plan-only
```

如果使用旧任务模板，仍然可以：

```bash
./automan run --task configs/tasks/tpcc-postgresql-template.yaml --plan-only
```

但新工作建议统一使用 `./configure -c pg,ym-heap -o automan.yml` 这类短别名生成配置。

## 4. 配置模板

### 4.1 生成配置

从模板生成本次任务配置：

```bash
./configure -c pg -o automan.yml
```

可选模板：

```text
pg
ym-heap
ym-mars3
pg,ym-heap
```

生成后的 `automan.yml` 是本次压测的主配置文件。

### 4.2 配置结构

配置采用类似 Ansible inventory 的结构：

```yaml
all:
  vars:
    benchmark: tpcc

  children:
    bench:
      hosts:
        bench01:
          ansible_host: 172.16.100.143
          ansible_user: root
          ansible_connection: local
          automan_workdir: /root/automan

    pg:
      vars:
        db_type: postgresql
        storage_engine: heap
        test_mode: single_node

        tpcc_warehouses: [100]
        tpcc_terminals: [100, 500]
        tpcc_load_workers: 32
        tpcc_run_mins: 15

        db_host: 192.168.100.29
        db_port: 5232
        db_name: postgres
        db_user: zhangchen
        db_password: ""
```

含义：

- `bench` 是执行 Automan 和 BenchmarkSQL 的机器。
- `pg`、`ymatrix_heap`、`ymatrix_mars3` 等 group 是压测目标。
- group 名默认就是 target id。
- `db_type + storage_engine + test_mode` 会匹配数据库 profile。
- `tpcc_warehouses` 与 `tpcc_terminals` 做笛卡尔积生成 run。

例如：

```yaml
tpcc_warehouses: [100, 1000]
tpcc_terminals: [100, 500, 1000]
```

会生成：

```text
100 仓 * 100 并发
100 仓 * 500 并发
100 仓 * 1000 并发
1000 仓 * 100 并发
1000 仓 * 500 并发
1000 仓 * 1000 并发
```

## 5. 数据库形态

当前 TPC-C 支持的 profile：

```text
postgresql_heap_single_node
ymatrix_heap_master_only
ymatrix_mars3_master_only
```

### 5.1 PostgreSQL

PostgreSQL 当前默认：

```yaml
db_type: postgresql
storage_engine: heap
test_mode: single_node
```

DDL 使用 BenchmarkSQL 默认 PostgreSQL 建表语句。

### 5.2 YMatrix heap

YMatrix heap 当前默认：

```yaml
db_type: ymatrix
storage_engine: heap
test_mode: master_only
```

DDL 使用默认 BenchmarkSQL PostgreSQL 建表语句。

### 5.3 YMatrix mars3

YMatrix mars3 当前默认：

```yaml
db_type: ymatrix
storage_engine: mars3
test_mode: master_only
```

MARS3 参数在配置中声明：

```yaml
mars3_options:
  prefer_load_mode: single
  rowstore_size: 64
  compresstype: zstd
  compresslevel: 1
  compress_threshold: 1200
```

对应建表语句会渲染为类似：

```sql
USING MARS3
WITH(
  mars3options='prefer_load_mode=single,rowstore_size=64',
  compresstype=zstd,
  compresslevel=1,
  compress_threshold=1200
)
DISTRIBUTED MASTERONLY;
```

## 6. 参数修改流程

### 6.1 原则

数据库参数修改必须由用户手工执行。

Automan 只生成命令：

```bash
./automan param -i automan.yml
```

`param` 会直接在屏幕输出参数修改命令和确认命令，不再生成 `manual-parameter-commands.sh`，也不会创建参数预审 job。

### 6.2 PostgreSQL 参数命令

PostgreSQL 会生成：

```text
ALTER SYSTEM SET <参数名> = <参数值>
show 每个参数
```

配置字段：

```yaml
database_parameters:
  max_connections: "550"
  shared_buffers: 62GB
  effective_cache_size: 175GB
  work_mem: 116MB
  maintenance_work_mem: 12GB
  checkpoint_timeout: 30min
  max_wal_size: 64GB
```

用户需要使用具备 `ALTER SYSTEM` 权限的数据库角色手工执行屏幕输出的命令。参数是否需要 reload 或 restart 由用户按数据库规则自行处理，Automan 不生成重启命令。

### 6.3 YMatrix 参数命令

YMatrix 会生成：

```text
gpconfig -c <参数名> -v <参数值>
show 每个参数
```

配置字段：

```yaml
gpconfig_command: gpconfig

database_parameters:
  max_connections: "550"
```

YMatrix master only 的 TPC-C 模板会基于 `host_facts.cpu_threads` 和 `host_facts.memory_gb`
自动补齐缺失的内存/WAL 参数，并仍然只生成手工执行脚本。自动补齐的参数包括：

```text
shared_buffers
effective_cache_size
work_mem
maintenance_work_mem
checkpoint_completion_target
max_wal_size
min_wal_size
vacuum_cost_limit
```

如果 `database_parameters` 中显式声明了某个参数，显式值优先；未声明的参数使用自动推荐值。

用户需要登录 YMatrix 管理节点，以具备权限的 OS 用户身份手工执行。

## 7. 校验

### 7.1 检查执行环境、数据库连通性和采集权限

```bash
./check.yml -i automan.yml
```

检查内容：

```text
Automan 目录是否存在
Python CLI 是否可导入
Java 是否存在
psql 是否存在
BenchmarkSQL 脚本是否存在
BenchmarkSQL dist jar 是否存在
数据库是否能 select 1
system 采集工具是否存在：vmstat/iostat/pidstat/mpstat
perf record 工具是否存在
perf record 权限是否可用
perf script/perf report 是否可导出
```

如果 `db_password` 留空，而数据库需要密码，`check.yml` 会失败。可以在本地 `automan.yml` 中填入密码，或使用临时 inventory 文件。

远程数据库主机的采集检查会通过 SSH 执行。SSH 主机默认复用 `db_host`；只有 SSH 入口和数据库连接地址不同时才需要填写 `config_host`。远程 SSH 仍然需要 `config_user` 和 `config_password`，当前不使用 SSH key 或 agent 自动登录。

如果采集权限不足，`check.yml` 会在压测前失败，并明确输出需要处理的权限或工具，例如：

```text
required: install perf and run as root, or grant CAP_PERFMON/CAP_SYS_ADMIN as appropriate
check: cat /proc/sys/kernel/perf_event_paranoid
check: cat /proc/sys/kernel/kptr_restrict
example temporary fix: sysctl kernel.perf_event_paranoid=-1
```

当前默认采集频率：

```yaml
collectors:
  system:
    interval_seconds: 1
  perf:
    phases: [runBenchmark.sh]
    frequency: 99
    call_graph: fp
```

含义：

```text
CPU/内存/IO 采集默认每 1 秒一次。
perf 使用 perf record，不生成火焰图。
perf 默认只覆盖 runBenchmark.sh 阶段，不覆盖建表导数阶段。
```

## 8. 正式压测

正式执行：

```bash
./tpcc.yml -i automan.yml
```

执行顺序：

```text
tpcc_prepare
  创建 runs/work 目录
  校验 inventory
  提醒参数手工修改

tpcc_run
  调用 automan run -i automan.yml
```

每个 run 的 BenchmarkSQL 顺序：

第一条 run：

```text
schema_probe
如果已存在 bmsql_* 对象，则 runDatabaseDestroy.sh
runDatabaseBuild.sh
runBenchmark.sh
```

后续 run：

```text
runDatabaseDestroy.sh
runDatabaseBuild.sh
runBenchmark.sh
```

注意：

- 每次压测都会重新 build。
- 除非第一条 run 的 schema probe 证明库里没有 `bmsql_*` 对象，否则会先 destroy。
- BenchmarkSQL 的 build/destroy 脚本即使返回 0，只要日志包含 `FATAL`、`ERROR`、`Exception`、`Failed to`、`password authentication failed`，Automan 也会判失败。
- `runBenchmark.sh` 返回 0 且日志中已经输出最终 `Measured tpmC` 时，Automan 会保留该次结果；中途事务错误仍保留在日志和报告里。

## 9. 只跑准备阶段

如果只想验证 `tpcc_prepare`，不要启动 BenchmarkSQL：

```bash
./tpcc.yml -i automan.yml --tags tpcc_prepare
```

这个命令只会：

```text
创建目录
校验 inventory
输出参数手工修改提醒
```

不会执行 `runDatabaseBuild.sh` 或 `runBenchmark.sh`。

## 10. 查看运行进度和完成结果

查看当前正在运行的 TPC-C job：

```bash
./automan progress
```

持续刷新，默认 5 秒：

```bash
./automan progress --watch
```

指定刷新间隔：

```bash
./automan progress --watch 5
```

指定 job：

```bash
./automan progress --job <job_id>
```

`progress` 自动识别当前 running job，输出 Pigsty 风格状态行。TPC-C 阶段含义如下：

```text
schema_probe: action=probe
runDatabaseDestroy.sh: action=destroy
runDatabaseBuild.sh: action=load
runBenchmark.sh: action=test
report: action=report
```

当阶段是 `runBenchmark.sh` 时，会显示 elapsed、expected、remain、pct。

`list` 用于查看已经完成并能解析性能指标的结果：

查看所有已完成并可解析性能指标的结果：

```bash
./automan list
```

查看指定 job：

```bash
./automan list --job <job_id>
```

每一行结果都有稳定的短 hash ID，例如 `a19f3c8b21`。这个 ID 来自完整 run id 的确定性 hash，不会因为排序变化而改变，并且可直接用于 delete。

按结果 ID 删除一个或多个 run/work 产物：

```bash
./automan delete <id>
./automan delete <id> <id>
```

跳过交互确认：

```bash
./automan delete <id> <id> -f
```

输出字段：

```text
ID: 可用于 delete 的稳定短 hash ID
Job: job id
Finished Results: 已完成并可解析性能结果的 run 数
TARGET: target id
DB HOST: 数据库地址
tpmC/tpmTOTAL: BenchmarkSQL 解析出的性能指标
```

## 11. 生成报告

生成最新 job 报告：

```bash
./report.yml -i automan.yml
```

或直接用 CLI：

```bash
./automan report
```

指定 job：

```bash
./automan report --job <job_id>
```

报告位置：

```text
runs/jobs/<job_id>/report/report.md
```

报告内容包括：

```text
job 状态
manual parameter commands 记录位置：resolved-plan.yaml
TPC-C matrix
target 信息
每个 run 的状态、仓数、并发数、runMins
BenchmarkSQL 解析结果：Measured tpmC、Measured tpmTOTAL、Session Start、Session End
资源采集产物索引：CPU/内存/IO 日志路径、文件大小、样本行数
perf record 产物索引：perf.data、perf.script.txt、perf.report.txt
失败摘要
关键错误日志片段
```

同时会生成供 agent 继续分析的结构化上下文：

```text
runs/jobs/<job_id>/report/agent-context.json
```

报告只展示客观事实和产物路径，不输出调优建议或复盘结论。

## 12. 清理 TPC-C 对象

显式清理当前 inventory 中所有 target 的 `bmsql_%` 对象：

```bash
./tpcc-rm.yml -i automan.yml
```

清理逻辑：

```text
读取 inventory
遍历每个 target
连接对应数据库
删除 current_schema() 下 relname like 'bmsql_%' 的表和序列
```

这是显式危险操作，只在用户主动执行 `tpcc-rm.yml` 时发生。正式压测中的 destroy 仍由 BenchmarkSQL 的 `runDatabaseDestroy.sh` 控制。

## 13. 结果目录

一次 job：

```text
runs/jobs/<job_id>/
  job.yaml
  resolved-plan.yaml
  job.json
  status.json
  timeline.jsonl
  report/
    report.md
    agent-context.json
```

一次 run：

```text
runs/<run_id>/
  resolved-task.yaml
  status.json
  logs/
    schema_probe.stdout.log
    schema_probe.stderr.log
    runDatabaseBuild.sh.stdout.log
    runDatabaseBuild.sh.stderr.log
    runBenchmark.sh.stdout.log
    runBenchmark.sh.stderr.log
  benchmark/
    result/
  collectors/
    runDatabaseBuild.sh/
      database/
        system/
          vmstat.log
          iostat-x.log
          pidstat-durh.log
          mpstat-P-ALL.log
    runBenchmark.sh/
      database/
        system/
          vmstat.log
          iostat-x.log
          pidstat-durh.log
          mpstat-P-ALL.log
        perf/
          perf.data
          perf.script.txt
          perf.report.txt
          perf-001.data
          perf-001.script.txt
          perf-001.report.txt
          samples.json
```

BenchmarkSQL 工作目录：

```text
work/tpcc/benchmarksql/<run_id>/
  tpcc.properties
  benchmarksql/
    run/
      sql.common/
      sql.postgres/
      runDatabaseDestroy.sh
      runDatabaseBuild.sh
      runBenchmark.sh
```

每个 run 使用独立 BenchmarkSQL 工作副本，避免 PostgreSQL heap、YMatrix heap、YMatrix mars3 的 DDL 相互覆盖。

## 14. 常见问题

### 14.1 `password authentication failed`

原因：

```text
automan.yml 中 db_password 为空或错误
```

处理：

```text
检查 db_user/db_password/db_host/db_port/db_name
先执行 ./check.yml -i automan.yml
确认 select 1 成功后再跑 tpcc.yml
```

### 14.2 `BenchmarkSQL dist jar is missing`

原因：

```text
tools/benchmarksql/dist/BenchmarkSQL-*.jar 不存在
```

处理：

```bash
python -m automan_core tools build-benchmarksql --host 172.16.100.143 --user root --remote-workdir /root/automan
```

然后同步项目到执行机。

### 14.3 `ansible-playbook: command not found`

原因：

```text
执行机没有安装 Ansible
```

处理：

```bash
python3.8 -m pip install 'ansible-core<2.14'
```

或用系统包管理器安装 Ansible。

### 14.4 `from __future__ import annotations` 报错

原因：

```text
使用了 Python 3.6 或更老版本执行 Automan
```

处理：

```bash
python3.8 -m automan_core --help
```

当前脚本入口默认使用 `python3.8`。

### 14.5 `list` 显示 failed 但不知道原因

处理：

```bash
./automan list --job <job_id>
cat runs/jobs/<job_id>/timeline.jsonl
cat runs/<run_id>/status.json
ls runs/<run_id>/logs/
```

重点看：

```text
last_error
runDatabaseBuild.sh.stderr.log
runBenchmark.sh.stdout.log
runBenchmark.sh.stderr.log
```

### 14.6 误跑或中断后如何清理

先确认没有进程：

```bash
ps -ef | egrep 'runDatabaseBuild|runBenchmark|LoadData|ansible-playbook' | grep -v egrep
```

清理数据库对象：

```bash
./tpcc-rm.yml -i automan.yml
```

确认对象数量：

```sql
select count(*)
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
where n.nspname = current_schema()
  and c.relname like 'bmsql_%';
```

## 15. 推荐操作 SOP

### 15.1 PostgreSQL 单目标 TPC-C

```bash
cd /root/automan

./configure -c pg -o automan.yml
vi automan.yml

./automan param -i automan.yml

# 手工执行屏幕输出的参数修改命令，并确认数据库参数生效

./check.yml -i automan.yml
./tpcc.yml -i automan.yml
./automan list
./report.yml -i automan.yml
```

### 15.2 YMatrix mars3 TPC-C

```bash
cd /root/automan

./configure -c ym-mars3 -o automan.yml
vi automan.yml

# 重点确认 mars3_options
./automan param -i automan.yml

# 手工执行 gpconfig 参数命令；如参数需要 reload/restart，由用户手工处理

./check.yml -i automan.yml
./tpcc.yml -i automan.yml
./automan list
./report.yml -i automan.yml
```

### 15.3 多目标对比

```bash
cd /root/automan

./configure -c pg,ym-heap -o automan.yml
vi automan.yml

./automan param -i automan.yml

# 分别手工应用每个数据库目标的参数

./check.yml -i automan.yml
./tpcc.yml -i automan.yml
./automan list
./report.yml -i automan.yml
```

当前实现要求一个 inventory 内的多个 target 使用同一组 TPC-C matrix。

## 16. 当前已验证状态

截至当前版本：

```text
GitHub main: 已推送
远端 /root/automan: 已同步
远端 HEAD: cc093c9
远端 Python 单测: 23 tests OK
远端 check.yml syntax-check: OK
远端 tpcc.yml syntax-check: OK
远端 report.yml syntax-check: OK
远端 tpcc-rm.yml syntax-check: OK
远端 ./check.yml 使用临时 PG 密码 inventory: OK
远端 ./tpcc.yml --tags tpcc_prepare: OK，未启动 BenchmarkSQL
远端 ./tpcc-rm.yml: OK
PG 当前 schema 下 bmsql_% 对象数: 0
```

远端仍有一个本地修改：

```text
configs/tasks/tpcc-postgresql-template.yaml
```

这是 legacy 模板里的本地密码配置，未纳入提交。新流程建议使用 `conf/tpcc/base.yml` 和 `conf/tpcc/targets/*.yml` 生成配置。
