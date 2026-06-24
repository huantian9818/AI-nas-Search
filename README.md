# AI NAS Search

AI NAS Search 是一个面向 QNAP NAS 的本地文件名索引与检索工具。它使用管理员配置的 NAS 索引账号定时同步文件夹和文件名到本机 SQLite 数据库，普通用户再用自己的 NAS 账号登录，系统根据该账号可访问的共享文件夹过滤浏览、搜索和 AI 问答结果。

这个项目的目标不是替代 NAS 文件管理器，而是把「大量 NAS 文件名检索」「按共享文件夹权限隔离」「基于搜索结果向 AI 提问」放到一个轻量 Web 应用里。

## 主要功能

- 多 NAS 配置：管理员可以维护多个 QNAP NAS。
- 定时同步：按 NAS 配置的间隔遍历共享文件夹，并更新有变化的文件名数据。
- 本地数据库搜索：搜索直接查本机 SQLite，避免每次搜索都扫 NAS。
- 权限过滤：普通用户用自己的 NAS 账号登录，只能看到有权限的共享文件夹。
- 统一登录入口：未登录时打开概览、目录或搜索，都会显示 NAS 账号登录窗口；登录成功后返回原页面。
- 登录状态：普通用户登录默认在浏览器保留 30 天，程序重启后会清空；登录后可从导航退出当前用户。
- 目录浏览：按用户权限浏览本地索引中的目录结构。
- NAS 直连下载：程序校验权限后跳转到 QNAP 下载地址，支持当前目录内多选批量下载，文件不经过本程序转发。
- 搜索命中目录树：搜索页只展示包含命中结果的目录分支。
- AI 问答：用户可以基于当前搜索结果向 AI 提问，AI 只能参考本次搜索 payload。
- AI 路径跳转：AI 回答中的已知目录或文件路径会自动链接到可访问目录。
- 管理员保护：设置页、手动同步、NAS 管理入口只对管理员开放。

## 工作方式

1. 管理员登录 `/admin/login`。
2. 管理员在 `/settings` 添加一个或多个 NAS，使用当前表单测试连接成功后保存索引账号。
3. 程序按同步计划或手动触发，从 NAS API 拉取文件夹和文件名，写入本机数据库。
4. 普通用户打开概览、目录或搜索页面，未登录时会看到 NAS 账号登录窗口。
5. 用户选择 NAS 并输入自己的 NAS 账号。
6. 程序通过 NAS API 判断该用户可访问哪些共享文件夹。
7. 登录成功后返回用户原本打开的页面；浏览、搜索和 AI 问答只读取该用户有权限的共享文件夹数据。

QNAP File Station 没有作为第一版依赖的稳定文件变化回调，所以当前每次定时同步都会遍历 NAS 目录树，再把新增、修改和删除结果更新到本地数据库。如果后续接入 QNAP Notification Center、Qmiix 或其他事件源，可以把事件作为提前触发同步的信号。

## 技术栈

- Python 3.12+
- FastAPI
- SQLAlchemy
- SQLite
- Jinja2
- httpx
- pytest

## 快速启动

推荐使用 `uv`：

```bash
uv sync
cp config.example.toml config.toml
uv run uvicorn nas_index.web.app:app --host 127.0.0.1 --port 8001 --reload
```

打开：

- 应用入口：`http://127.0.0.1:8001/`
- 管理员登录：`http://127.0.0.1:8001/admin/login`

普通用户不需要寻找单独的登录导航。未登录时打开概览、目录或搜索，页面会自动显示 NAS 账号登录窗口。

如果你不用 `uv`，也可以使用本地虚拟环境：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
uvicorn nas_index.web.app:app --host 127.0.0.1 --port 8001 --reload
```

## 配置文件

项目会默认读取根目录下的 `config.toml`。该文件已被 `.gitignore` 忽略，不应该提交到 GitHub。

示例：

```toml
[app]
admin_password = "change-me"
scan_concurrency = 4
user_access_ttl_seconds = 2592000

[ai]
api_key = ""
base_url = "https://api.openai.com/v1"
model = "deepseek-v4"
timeout_seconds = 30
max_tokens = 700
```

也可以使用环境变量覆盖配置，前缀为 `NAS_INDEX_`。例如：

```bash
NAS_INDEX_ADMIN_PASSWORD="change-me" \
NAS_INDEX_AI_API_KEY="sk-..." \
uv run uvicorn nas_index.web.app:app --host 127.0.0.1 --port 8001
```

常用配置：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `database_url` | `sqlite:///data/nas-index.db` | 本机索引数据库 |
| `log_dir` | `logs` | 日志目录 |
| `scan_page_size` | `500` | NAS API 分页大小 |
| `scan_batch_size` | `500` | 数据库批量写入大小 |
| `scan_concurrency` | `4` | 同步并发数 |
| `scan_skip_recycle` | `true` | 是否跳过 `@Recycle` |
| `qnap_timeout_seconds` | `20` | QNAP API 超时 |
| `qnap_retry_attempts` | `3` | QNAP API 重试次数 |
| `user_access_ttl_seconds` | `2592000` | 普通用户登录有效期，默认30天；程序重启后清空 |
| `sync_scheduler_poll_seconds` | `10` | 同步调度轮询间隔 |
| `admin_password` | 空 | 管理员密码 |
| `admin_session_ttl_seconds` | `3600` | 管理员会话有效期 |
| `ai.api_key` | 空 | AI API Key |
| `ai.base_url` | `https://api.openai.com/v1` | OpenAI 兼容接口地址 |
| `ai.model` | `deepseek-v4` | AI 模型名称 |
| `ai.timeout_seconds` | `30` | AI 请求超时 |
| `ai.max_tokens` | `700` | AI 最大输出长度 |

测试同步速度时可以先把 `config.toml` 里的
`scan_concurrency` 调到 `8`，再重启程序。概览页的同步状态会显示
已处理条数、耗时、平均速度、当前路径、进程内存和 CPU 采样值，便于和
并发 `4` 的表现对比。

## 管理员使用

1. 在 `config.toml` 中设置 `admin_password`。
2. 启动程序。
3. 打开 `/admin/login` 输入管理员密码。
4. 进入 `/settings` 添加 NAS。
5. 填写 NAS 地址、端口、HTTP/HTTPS、索引账号和密码。
6. 设置同步间隔。
7. 点击“测试连接”；只有当前连接信息测试成功后才能保存。
8. 保存后可等待调度器自动同步。
9. 如需在概览页手动触发同步，管理员还需使用 NAS 用户账号完成普通用户登录；管理员身份与 NAS 用户权限相互独立。

索引账号建议使用专门创建的只读账号，并授权所有需要被索引的共享文件夹。该账号密码会保存在本机 SQLite 数据库中，请只在可信设备上运行本程序。

## 普通用户使用

1. 打开概览 `/`、目录 `/browse` 或搜索 `/search`。
2. 未登录时，当前页面会显示 NAS 账号登录窗口。
3. 选择 NAS，输入自己的 NAS 用户名和密码。
4. 程序会向 NAS 验证账号，并读取该账号可访问的共享文件夹。
5. 登录成功后返回原本打开的页面；搜索关键词和目录路径会保留。
6. 导航只显示“概览、目录、搜索”；登录后额外显示“退出当前用户”。
7. 普通用户登录状态默认在浏览器保留 30 天，关闭浏览器不会退出；程序重启后内存会话清空，需要重新登录。
8. 在目录页下载文件时，程序只负责校验权限并跳转到 NAS 下载接口，下载流量由 NAS 直接提供；批量下载支持同一目录下的多个文件。

普通用户密码只用于当次 NAS 权限验证，不会写入 SQLite。

## 搜索与 AI 问答

搜索流程：

1. 用户输入关键词。
2. 后端先按用户可访问共享文件夹过滤数据。
3. 在过滤后的本地索引中搜索文件名和文件夹名。
4. 页面展示完整命中目录树。
5. 同时生成一份签名后的搜索 payload，供 AI 问答使用。

AI 问答流程：

1. 用户在搜索结果页输入问题。
2. 前端把当前搜索 payload 和问题发给 `/search/summary`。
3. 后端验证 payload 签名和当前用户权限。
4. AI 只拿到本次搜索结果中的目录名、文件名和路径。
5. AI 回答里的已知路径会被前端转换为目录链接。

如果 AI 提到文件路径，链接会打开该文件所在的父目录；如果 AI 提到目录路径，链接会直接打开该目录。

## 数据目录

默认会创建：

- `data/nas-index.db`：SQLite 索引数据库
- `logs/nas-index.log`：应用日志

这些文件包含本地运行数据，已被 `.gitignore` 忽略。

## 测试

```bash
uv run pytest
```

或：

```bash
. .venv/bin/activate
pytest
```

当前测试覆盖：

- 数据库初始化和迁移
- 多 NAS 配置
- NAS 权限验证
- 用户访问会话
- 文件索引同步
- 搜索权限过滤
- 管理员登录保护
- AI 搜索 payload 和问答接口

## 真实 NAS 验收建议

1. 配置管理员密码并启动程序。
2. 登录管理员。
3. 添加一台 QTS 5.2.9 NAS。
4. 使用只读索引账号通过连接测试。
5. 手动触发首次同步。
6. 打开概览、目录或搜索页面，使用普通 NAS 用户完成登录。
7. 确认目录页只显示该用户可访问的共享文件夹。
8. 搜索一个文件名关键词，确认搜索结果只来自可访问共享文件夹。
9. 在 NAS 新增、重命名、删除文件后再次同步。
10. 确认本地索引随同步更新。
11. 在搜索页向 AI 提问，确认回答只引用当前搜索结果。

## 安全注意事项

- 不要提交 `config.toml`、数据库、日志或任何真实密码。
- 建议为索引创建独立只读 NAS 账号。
- 管理员密码应设置为强密码。
- 普通用户密码不会落库，但会在登录时发送到对应 NAS API。
- 普通用户浏览器 Cookie 最长保留 30 天，但服务端会话只存在程序内存中，程序重启后旧 Cookie 会失效。
- AI 问答不会读取文件内容，只会使用搜索结果中的路径、目录名和文件名。
- 如果部署到局域网以外，请在前面加反向代理、HTTPS 和额外访问控制。

## 项目状态

当前版本适合在可信内网环境中运行，用于 QNAP NAS 文件名索引、权限过滤搜索和基于搜索结果的 AI 辅助问答。
