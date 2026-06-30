# AP 分析查询与 TPC-H 测试方案

## 当前状态

`ap-query`、`tpch-load`、`tpch-query` 已经接入 Automan 的统一流程，和 TPC-C、TS 阶段同级。

统一入口如下：

```bash
./configure -t ap -c ym-mars3 -o automan.yml
./configure -t tpch -c pg,ym-heap,ym-mars3 -o automan.yml
./check.yml -i automan.yml
./automan run -i automan.yml --stage ap-query
./automan run -i automan.yml --stage tpch-load
./automan run -i automan.yml --stage tpch-query
./automan list -t ap --job <job_id>
./automan list -t tpch --job <job_id>
```

`./bin/validate -i automan.yml` 不需要用户手工执行，`check.yml` 会自动走校验。

## AP 与 TS Query 的区别

`ts-query` 面向时序明细查询，典型模式是 VIN 加时间范围的窄范围查询，用来观察时序查询速度。

`ap-query` 面向分析型查询，数据源可以复用时序写入后的车辆明细表，但查询模式是跨 VIN、跨时间窗口、多指标聚合、排序或分组统计。它关注扫描、聚合、过滤和返回规模较大的分析查询能力。

因此两者可以复用同一批 `iot_vehicle_raw_ct<compress_threshold>` 表，但结果指标不同：

- `ts-query`：短窗口查询延迟、返回行数、P50/P95/P99。
- `ap-query`：分析 SQL 平均耗时、P50/P95/P99、返回行数、错误数、表数据大小。

## AP 场景

配置类型为 `ap`，阶段为 `ap-query`。

默认配置：

```yaml
benchmark: ap
ap_stages: [ap-query]
compress_threshold: [1200, 4096, 8192, 32768]
ap_query:
  source_table: iot_vehicle_raw
  rounds: [3]
  warmup_rounds: [1]
  timeout_seconds: 7200
  query_set: vehicle_ap_basic
```

计划生成规则：

- 每个 `compress_threshold` 生成一个源表名：`iot_vehicle_raw_ct1200`、`iot_vehicle_raw_ct4096` 等。
- 每个阈值执行一组 AP SQL。
- 默认 SQL 集目录为 `benchmarks/ap/queries/vehicle_ap_basic`。
- `list -t ap` 展示同一列 `Table Data Size`，用于观察不同 `compress_threshold` 下的表数据大小差异。

执行器行为：

- 读取 `query_set` 对应目录下的全部 `.sql` 文件。
- 支持 SQL 模板变量：`{{schema}}`、`{{source_table}}`、`{{table}}`。
- 先执行 `warmup_rounds`，不计入结果。
- 再执行 `rounds`，统计 `query_count`、`avg_ms`、`p50_ms`、`p95_ms`、`p99_ms`、`rows_returned`、`errors`。
- 查询结束后采集源表数据大小。
- 结果写入 `runs/<run_id>/result.json` 和 `runs/<run_id>/benchmark/ap-query/result.json`。

## TPC-H 场景

配置类型为 `tpch`，阶段为 `tpch-load` 和 `tpch-query`。

默认配置：

```yaml
benchmark: tpch
tpch_stages: [tpch-load, tpch-query]
compress_threshold: [1200, 4096, 8192, 32768]
tpch:
  scale_factors: [1]
  query_streams: [1]
  run_mins: [0]
  query_set: standard
  data_dir: benchmarks/tpch/data/sf{scale_factor}
  schema_dir: benchmarks/tpch/schema
  query_dir: benchmarks/tpch/queries
```

TPC-H 建表分三类：

- `pg`：PostgreSQL heap 表，DDL 位于 `benchmarks/tpch/schema/pg/schema.sql`。
- `ym-heap`：YMatrix heap 表，DDL 位于 `benchmarks/tpch/schema/ym-heap/schema.sql`。
- `ym-mars3`：YMatrix MARS3 表，DDL 位于 `benchmarks/tpch/schema/ym-mars3/schema.sql`。

配置方式：

```bash
./configure -t tpch -c pg,ym-heap,ym-mars3 -o automan.yml
```

计划生成规则：

- `pg` 和 `ym-heap` 不展开 `compress_threshold`，因为压缩阈值只作用于 MARS3。
- `ym-mars3` 按 `compress_threshold` 矩阵展开。
- 每个目标按 `tpch-load`、`tpch-query` 生成同级测试结果。
- `list -t tpch` 展示 `DDL Profile`、`Compress Threshold`、`SF`、`Streams`、`Table Data Size`、耗时和 `Queries/h`。该指标是内部吞吐速率，不作为正式 TPC-H QphH。

`tpch-load` 执行器行为：

- 按目标类型选择 `pg`、`ym-heap`、`ym-mars3` DDL。
- MARS3 DDL 会注入当前 `compress_threshold`。
- 从 `data_dir` 读取 `region.tbl`、`nation.tbl`、`supplier.tbl`、`customer.tbl`、`part.tbl`、`partsupp.tbl`、`orders.tbl`、`lineitem.tbl`，也支持 `.tbl.gz`。
- 自动去除 TPC-H `.tbl` 行尾多余分隔符，再使用 psql `\copy` 导入。
- 导入完成后执行 `analyze`。
- 采集 8 张表的总数据大小。

`tpch-query` 执行器行为：

- 默认查询目录为 `benchmarks/tpch/queries/standard`。
- 当前提供 `q01.sql` 到 `q22.sql` 共 22 条可执行 SQL。
- 支持 SQL 模板变量：`{{schema}}`。
- 当 `run_mins` 为 `0` 时，每个 query stream 执行一轮 22 条 SQL。
- 当 `run_mins` 大于 `0` 时，循环执行 query stream，直到达到测试时长。
- 统计 `query_count`、`avg_ms`、`p95_ms`、`errors`、`elapsed_seconds`、`queries_per_hour`、兼容字段 `qphh` 和表数据大小。

## 数据准备

AP 查询复用 TS 写入结果，运行前需要已经存在对应表，例如：

```text
public.iot_vehicle_raw_ct1200
public.iot_vehicle_raw_ct4096
```

TPC-H load 运行前需要把 dbgen 生成的数据放到对应目录，例如 SF1：

```text
benchmarks/tpch/data/sf1/
  region.tbl
  nation.tbl
  supplier.tbl
  customer.tbl
  part.tbl
  partsupp.tbl
  orders.tbl
  lineitem.tbl
```

如果数据目录或文件缺失，`tpch-load` 会失败并在 `result.json`、`status.json` 和 logs 中记录原因。

## 结果展示

AP 短表重点列：

- `ID`
- `Status`
- `Compress Threshold`
- `Table Data Size`
- `Rounds`
- `Query Count`
- `Avg`
- `P95`
- `Rows Returned`
- `Errors`

TPC-H 短表重点列：

- `ID`
- `Stage`
- `Status`
- `DDL Profile`
- `Compress Threshold`
- `SF`
- `Streams`
- `Table Data Size`
- `Elapsed Seconds`
- `Queries/h`
- `Errors`

## 已验证内容

已通过 focused unittest：

```bash
python -m unittest tests.test_ap tests.test_tpch tests.test_cli_contract tests.test_list_results tests.test_ts
```

结果：52 个测试通过。
