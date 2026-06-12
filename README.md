# QNAP File Index

本机只读索引 QTS 5.2.9 中的共享目录，并提供目录浏览和名称搜索。

## 启动

```bash
uv sync
uv run uvicorn nas_index.web.app:app --reload
```

打开 `http://127.0.0.1:8000/settings`，填写 NAS 主机、端口、HTTP/HTTPS、
只读账号和密码，然后先执行“测试连接”，再返回概览页开始扫描。

## QTS 账号

新建专用账号，授予需要索引的全部共享目录只读权限，并关闭该账号的两步验证。
网页中保存的密码会以明文写入本机 SQLite，请仅在可信设备上运行。

## 数据与日志

- 默认数据库：`data/nas-index.db`
- 数据库覆盖：`NAS_INDEX_DATABASE_URL=sqlite:////absolute/path/index.db`
- 默认日志目录：`logs`
- 日志目录覆盖：`NAS_INDEX_LOG_DIR=/absolute/path/logs`

首次扫描 10 万至 100 万条记录可能耗时较长。扫描失败或程序中断时会保留旧索引；
只有完整成功的扫描才会删除已不存在的旧记录。

## 真实 NAS 验收

1. 保存 QTS 5.2.9 设置并通过连接测试。
2. 确认所有只读账号可见共享目录都出现在目录页。
3. 在 NAS 新增文件、修改文件时间并删除文件，然后再次扫描。
4. 确认新增项出现、修改时间更新、删除项消失。
5. 临时撤销一个子目录的读取权限并扫描，确认任务失败且旧索引仍然保留。
6. 恢复权限后重新扫描，确认任务成功。

## 测试

```bash
uv run pytest
```
