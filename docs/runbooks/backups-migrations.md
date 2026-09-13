# 备份、恢复与停止写入后的迁移

本页提供真实维护工具，不配置生产服务或自动发布。运行前读取根 AGENTS、当前 PROJECT_STATE 和 recovery.md；保留每条命令的结果和工具输出的文件/逻辑哈希。备份复制使用 SQLite backup API（数据库自身提供的一致性复制接口），不直接复制正在写入的主文件。

## 先确认没有写入者

首版 `demo`、`replay`、`research` 是一次性命令；等待命令结束再备份，不能同时启动新的演示或回放写入同一运行目录。内外部两个数据库的一致性要求它们在复制期间都停止写入；工具保证单库复制，并不把两个独立数据库变成跨库原子事务。

首版没有已实现的常驻服务或 `stop` 命令，因此不提供虚构停止接口，也不使用 `pkill` 杀死其他任务。未来持续模拟/生产部署必须先增加经验证的停止、唯一执行者和重新核对流程，才能进入对应迁移阶段。

## 备份与完整性校验

在仓库根目录运行。`artifacts/demo` 必须是已成功结束的演示目录，`artifacts/backups/demo-v1` 必须使用新的备份名称。

```bash
uv run --offline --locked python -m tools.backup_sqlite verify --database artifacts/demo/internal.sqlite
uv run --offline --locked python -m tools.backup_sqlite verify --database artifacts/demo/broker.sqlite
uv run --offline --locked python -m tools.backup_sqlite backup --source artifacts/demo/internal.sqlite --output artifacts/backups/demo-v1/internal.sqlite
uv run --offline --locked python -m tools.backup_sqlite backup --source artifacts/demo/broker.sqlite --output artifacts/backups/demo-v1/broker.sqlite
uv run --offline --locked python -m tools.backup_sqlite verify --database artifacts/backups/demo-v1/internal.sqlite
uv run --offline --locked python -m tools.backup_sqlite verify --database artifacts/backups/demo-v1/broker.sqlite
```

源连接为只读；缺源不会自动建空库；目标必须不存在，同名文件或符号链接拒绝覆盖。工具运行 `PRAGMA integrity_check`，比较复制前后的表结构、数据与 SQLite `user_version` 逻辑摘要，并输出源/目标字节哈希。源在复制期间变化导致逻辑摘要不同会报错，不能忽略后继续切换。活跃写入时，源文件字节哈希与逻辑快照不能假定对应同一瞬间，因此可审查的备份仍必须先停止或隔离写入者。备份时还要保留整个运行目录的 manifest、输入、配置、版本、journal、报告与依赖锁，数据库文件本身不足以解释一次运行。

## 恢复到新目录

```bash
uv run --offline --locked python -m tools.backup_sqlite restore --source artifacts/backups/demo-v1/internal.sqlite --output artifacts/restored/demo-v1/internal.sqlite
uv run --offline --locked python -m tools.backup_sqlite restore --source artifacts/backups/demo-v1/broker.sqlite --output artifacts/restored/demo-v1/broker.sqlite
uv run --offline --locked python -m tools.backup_sqlite verify --database artifacts/restored/demo-v1/internal.sqlite
uv run --offline --locked python -m tools.backup_sqlite verify --database artifacts/restored/demo-v1/broker.sqlite
uv run --offline --locked quant-core replay --run-dir artifacts/demo --output artifacts/replayed-after-backup
```

恢复工具只复制和校验，不自动把新库投入交易。两库检查通过后，仍需核对订单、持仓、现金、费用及交错 journal；最后一条回放使用完整原运行证据，在另一新目录独立验证，不把恢复数据库文件冒充完整运行目录。真实账户还必须核对恢复点之后的外部成交，不能丢弃已经发生的事实。

## 契约生成与迁移边界

共同消息版本当前为 `1.0.0`；首版没有历史生产库迁移，也没有通用 SQL 自动迁移命令。SQLite 的 `PRAGMA user_version` 是数据库字段，不能与消息 `schema_version` 混为一谈。新版本迁移必须先编写 ADR、兼容/回放测试、备份和回滚步骤，再在停止写入的复制环境验证；未完成这些工作不得直接对运行库执行猜测的 ALTER TABLE。

以下命令已实现，用于维护**可再生成的源代码契约文件**：

```bash
uv run --offline --locked python -m tools.export_contracts --check
uv run --offline --locked python -m tools.export_contracts --write
uv run --offline --locked python -m tools.export_contracts --check
uv run --offline --locked quant-core check
```

默认或 `--check` 只比较；明确 `--write` 更新当前权威模型对应的 Schema 和固定正反样例。若发现未知旧 Schema，工具在写入前失败并保留它，要求明确迁移方案；不删除旧文件来获得通过。允许更新生成的 JSON Schema，不代表允许覆盖已批准数据快照、变更生产依赖或自行批准发布。日常只有在对应逻辑/契约变更已获授权并同步测试、文档后才使用 `--write`。
