# 配置模板解耦设计

配置按维度拆分，任务文件只负责组合与覆盖。

## 模板类型

```text
configs/databases/
  数据库连接、协议、默认 schema/database 命名规则。

configs/benchmarks/
  benchmark 类型、数据规模、SQL 路径、轮次、阶段默认行为。

configs/collectors/
  系统、数据库、perf 等采集策略。

configs/tasks/
  一次具体实验，引用数据库模板、benchmark 模板和采集模板，并声明本次运行的覆盖项。
```

## 当前 v0.1 文件

```text
configs/databases/ymatrix.yaml
configs/databases/doris.yaml
configs/benchmarks/tpch.yaml
configs/benchmarks/tpcc.yaml
configs/collectors/default.yaml
configs/tasks/ymatrix-vs-doris-tpch.yaml
docs/tools-management.md
```

## 组合原则

不要按所有组合预先生成完整模板。新增能力时只增加对应维度：

```text
新增数据库:
  添加 configs/databases/<db>.yaml

新增 benchmark:
  添加 configs/benchmarks/<benchmark>.yaml

新增采集策略:
  添加 configs/collectors/<profile>.yaml

新增实验:
  添加 configs/tasks/<experiment>.yaml
```

例如新增 TDengine 与 TPC-DS 时，不需要复制已有 Ymatrix-Doris TPC-H 模板，只需要新增：

```text
configs/databases/tdengine.yaml
configs/benchmarks/tpcds.yaml
configs/tasks/ymatrix-vs-tdengine-tpcds.yaml
```

## 解析规则

控制器读取 `configs/tasks/*.yaml` 后按以下顺序解析：

```text
1. 读取任务文件。
2. 加载 include.databases。
3. 加载 include.benchmark。
4. 加载 include.collectors。
5. 合并 run、ansible、phases、archive、report。
6. 应用 overrides。
7. 生成 resolved-task.yaml。
8. 对 secret 字段做脱敏后归档。
```

## 覆盖规则

任务文件中的 `overrides` 只描述本次实验差异，不重复基础模板内容。

示例：

```yaml
overrides:
  benchmark:
    scale_factor: 10
    rounds: 5

  databases:
    ymatrix:
      target_schema: tpch_sf10
    doris:
      database: tpch_sf10
```

## 代码解耦方向

后续 Python 代码也按相同维度拆分：

```text
src/testctl/db/
src/testctl/benchmarks/
src/testctl/collectors/
src/testctl/runner/
```

核心原则：

```text
数据库适配器只处理数据库差异。
Benchmark 适配器只处理测试模型差异。
Collector 只处理指标采集。
Runner 只负责编排阶段和产物归档。
```

## 工具路径

外部 benchmark 工具不放在 benchmark 模板目录里，统一放在 `tools/` 下。

例如 TPC-C 使用 BenchmarkSQL：

```text
tools/benchmarksql/
benchmarks/tpcc/benchmarksql/props.template
work/tpcc/benchmarksql/
```

具体规则见 `docs/tools-management.md`。
