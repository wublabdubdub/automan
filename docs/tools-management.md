# 工具管理规则

外部 benchmark 工具统一放在项目的 `tools/` 目录下，测试模板只引用工具路径，不把工具安装包、源码或运行目录散落到各处。

## 目录约定

```text
tools/
  benchmarksql/
    README.md
    bin/
    lib/
    run/

benchmarks/
  tpcc/
    benchmarksql/
      props.template
      README.md

work/
  tpcc/
    benchmarksql/

runs/
  <run_id>/
```

## 职责划分

`tools/benchmarksql/`
: BenchmarkSQL 工具本体。可以是解压后的 release，也可以是固定版本源码构建后的产物。

`benchmarks/tpcc/benchmarksql/`
: 本项目维护的 BenchmarkSQL 配置模板、数据库适配说明和必要 SQL 补丁。

`work/tpcc/benchmarksql/`
: 每次执行前生成的临时配置、运行脚本和中间文件。这个目录可以清理重建。

`runs/<run_id>/`
: 测试结果归档。每次运行都必须把实际使用的 BenchmarkSQL 配置、stdout、stderr、结果文件和摘要复制到这里。

## 版本记录

每次运行需要记录 BenchmarkSQL 的版本信息，建议写入：

```text
runs/<run_id>/env/tools/benchmarksql.json
```

内容至少包括：

```json
{
  "name": "benchmarksql",
  "home": "tools/benchmarksql",
  "version": "recorded-at-runtime",
  "git_commit": "recorded-if-available",
  "java_version": "recorded-at-runtime"
}
```

## 不直接提交的内容

以下内容后续应通过 `.gitignore` 排除：

```text
tools/benchmarksql/run/
work/
runs/
*.log
*.tmp
```

如果工具包过大，也可以只提交 `tools/benchmarksql/README.md`，通过 Ansible 或安装脚本下载、构建并校验版本。

## 当前 BenchmarkSQL 来源

当前本地工具目录：

```text
tools/benchmarksql/
```

来源仓库：

```text
https://github.com/petergeoghegan/benchmarksql.git
```

当前提交：

```text
f2f39cf42216a7b9f912eed07ccfb38522d02a21
```

构建要求：

```text
JDK 8
Ant
```

TPC-C 固定执行顺序：

```text
tools/benchmarksql/run/runDatabaseDestroy.sh
tools/benchmarksql/run/runDatabaseBuild.sh
tools/benchmarksql/run/runBenchmark.sh
```

框架实现时必须对每次 TPC-C run 执行完整三段流程，不允许直接跳过 destroy/build 后单独运行 benchmark。
