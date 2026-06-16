# QNAP File Index

本机只读索引 QTS 5.2.9 中多个 NAS 的共享目录，并提供按用户权限过滤的目录浏览和名称搜索。

## 启动

```bash
uv sync
uv run uvicorn nas_index.web.app:app --reload
```

打开 `http://127.0.0.1:8000/settings`，添加一个或多个 NAS，并为每个 NAS 填写主机、
端口、HTTP/HTTPS、只读索引账号和密码。保存后可以在概览页手动触发单个 NAS 同步。

## 多 NAS 与权限

管理员在 `/settings` 中添加一个或多个 NAS。每个 NAS 使用一个只读索引账号同步本地文件名索引。
索引账号需要授予全部应被索引的共享目录只读权限，并关闭两步验证。管理员索引账号密码会以明文写入本机 SQLite，请仅在可信设备上运行。

普通用户从 `/access` 选择 NAS 并输入自己的 NAS 账号密码。程序只临时使用该账号读取可访问共享文件夹列表，不会把用户密码写入 SQLite。浏览和搜索只返回该用户可访问共享文件夹下的本地索引数据。

## 同步

程序启动后会按 NAS 的同步间隔调度同步任务，也可以在概览页手动触发单个 NAS 同步。同步成功后会记录每个共享目录的下一次同步时间；同步失败时保留旧索引，避免因为网络、权限或 NAS API 错误误删本地记录。

QNAP File Station 没有作为第一版依赖的可靠文件变化回调。本程序以定时增量同步为准；后续接入 QNAP Notification Center 或 Qmiix 时，可以把事件作为提前触发同步的信号。

## 数据与日志

- 默认数据库：`data/nas-index.db`
- 数据库覆盖：`NAS_INDEX_DATABASE_URL=sqlite:////absolute/path/index.db`
- 默认日志目录：`logs`
- 日志目录覆盖：`NAS_INDEX_LOG_DIR=/absolute/path/logs`
- 默认扫描并发：`4`
- 扫描并发覆盖：`NAS_INDEX_SCAN_CONCURRENCY=4`
- 默认批量写入：`500`
- 批量写入覆盖：`NAS_INDEX_SCAN_BATCH_SIZE=500`
- 默认进度刷新间隔：`2` 秒
- 进度刷新覆盖：`NAS_INDEX_SCAN_PROGRESS_INTERVAL_SECONDS=2`
- 默认跳过回收站目录 `@Recycle`
- 回收站过滤覆盖：`NAS_INDEX_SCAN_SKIP_RECYCLE=0`
- 默认用户访问会话有效期：`900` 秒
- 用户访问会话覆盖：`NAS_INDEX_USER_ACCESS_TTL_SECONDS=900`
- 默认同步调度轮询间隔：`10` 秒
- 同步调度轮询覆盖：`NAS_INDEX_SYNC_SCHEDULER_POLL_SECONDS=10`

首次同步 10 万至 100 万条记录可能耗时较长。同步成功的目录会删除该目录下已经不存在的直接子项；同步失败或程序中断时会保留未成功列举目录的旧索引。

## 真实 NAS 验收

1. 在设置页新增 QTS 5.2.9 NAS 并通过连接测试。
2. 在概览页触发该 NAS 同步。
3. 从 `/access` 使用普通 NAS 用户登录，确认目录页只显示该用户可访问的共享目录。
4. 在 NAS 新增文件、修改文件时间并删除文件，然后再次同步。
5. 确认新增项出现、修改时间更新、删除项消失。
6. 临时撤销一个子目录的读取权限并同步，确认任务失败且旧索引仍然保留。
7. 恢复权限后重新同步，确认任务成功。

## 测试

```bash
uv run pytest
```
