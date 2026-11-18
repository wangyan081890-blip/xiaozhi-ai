# 云端部署教程（从零到上线）

本文把「小智 AI 云端版」部署到一台公网服务器，最终得到
`https://你的域名/`（或 `http://服务器IP/`）可访问的多用户在线助手。

> 前提：你需要一台云服务器、一个在线模型 API Key。
> 项目本身不含服务器与 Key，部署动作必须由你（或你在服务器上）执行。

---

## 0. 准备清单

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| 云服务器 | 1 核 1G 起，推荐 2 核 2G | Ubuntu 22.04 / 24.04 LTS；香港/新加坡节点访问国内 API 更稳 |
| 系统盘 | 20 GB+ | 镜像约 300 MB，数据库很小 |
| 域名 | 可选 | 有域名才能自动 HTTPS；没有可先用 IP + HTTP |
| 在线模型 Key | 至少一个 | DeepSeek / 硅基流动 / 智谱 / OpenAI 任一 |
| 本地项目 | 本仓库 | 上传到服务器即可 |

费用参考：轻量服务器约 ¥30–60/月；在线模型按 token 计费。

---

## 1. 登录服务器

在本地终端（Windows 用 PowerShell / 终端）：

```bash
ssh root@你的服务器IP
```

首次连接输入 `yes`，再输入服务器密码（或使用密钥）。

---

## 2. 安装 Docker

```bash
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version
```

把当前用户加入 docker 组（可选，免 sudo）：

```bash
usermod -aG docker root
```

---

## 3. 上传项目

**方式一：本地打包上传（推荐，简单）**

在本地项目目录执行：

```powershell
# Windows PowerShell
tar -czf xiaozhi-cloud.tar.gz -C C:\Users\lenovo\Documents\deepseek-harness\default-workspace xiaozhi-cloud
scp xiaozhi-cloud.tar.gz root@服务器IP:/opt/
```

服务器上：

```bash
cd /opt
tar -xzf xiaozhi-cloud.tar.gz
cd xiaozhi-cloud
```

**方式二：服务器直接拉取 Git 仓库**（如果你已把项目推到 GitHub/Gitee）

```bash
cd /opt
git clone <你的仓库地址> xiaozhi-cloud
cd xiaozhi-cloud
```

---

## 4. 配置环境变量

```bash
cp .env.example .env
nano .env
```

至少修改这四项：

```ini
SITE_ADDRESS=xiaozhi.example.com      # 有域名就填；没有则留空
POSTGRES_PASSWORD=换成至少16位强密码
SECRET_KEY=运行下面命令生成
DEEPSEEK_API_KEY=sk-你的key
```

生成 `SECRET_KEY`：

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

> 说明：
> - `POSTGRES_PASSWORD` 会拼进数据库连接串，请使用**不含 `@ : / ? #` 等字符**的字母数字密码。
> - `DEFAULT_PROVIDER` 默认会自动选「第一个已配置的在线服务」，一般不用改。
> - 想用其他服务商，填对应 `SILICONFLOW_API_KEY` / `ZHIPU_API_KEY` / `OPENAI_API_KEY`，
>   或填 `CUSTOM_API_KEY` + `CUSTOM_BASE_URL` + `CUSTOM_MODEL` 三项使用任意兼容接口。
> - `.env` 含密钥，不要提交到 Git、不要发到群里。

---

## 5. 配置域名（可选）

在域名服务商处添加 A 记录：

```
主机记录：xiaozhi（或 @）
记录类型：A
记录值：你的服务器公网 IP
```

等待 1–10 分钟生效，可用 `ping xiaozhi.example.com` 检查。
同时确认云服务器安全组放行 **80 / 443** 端口。

---

## 6. 启动

```bash
docker compose up -d --build
docker compose ps
```

首次会构建镜像（约 1–3 分钟），随后：

- `app`：FastAPI 服务（仅容器内 8000）
- `db`：PostgreSQL 16（仅容器内 5432）
- `caddy`：反向代理 + 自动 HTTPS（对外 80/443）

查看日志：

```bash
docker compose logs -f app
docker compose logs -f caddy
```

---

## 7. 验证上线

服务器本机：

```bash
curl -s http://127.0.0.1:8000/healthz
```

应返回类似：

```json
{"ok":true,"app":"小智 AI","db":{"ok":true,...}}
```

外网验证：

```bash
curl -I https://xiaozhi.example.com/
```

浏览器打开：

- 有域名：`https://xiaozhi.example.com/`
- 无域名：`http://服务器IP/`

检查项：

1. 页面能打开，顶部显示「在线模型已连接 · DeepSeek」。
2. 输入「你好」能得到流式回答。
3. 刷新页面，左侧历史会话仍在。
4. 手机浏览器打开同一网址，能正常对话。
5. 用另一台设备/浏览器打开，看不到第一台设备的会话（多用户隔离）。
6. `docker compose restart app` 后历史仍在（数据库持久化）。

---

## 8. 日常运维

```bash
docker compose ps                 # 状态
docker compose logs -f app        # 应用日志
docker compose restart app        # 重启应用
docker compose pull && docker compose up -d --build   # 更新镜像后重新部署
docker compose down               # 停止（保留数据库卷）
docker compose down -v            # 停止并删除数据（慎用！）
```

容器均设置 `restart: unless-stopped`，服务器重启或进程崩溃会自动拉起。

### 数据库备份

```bash
# 备份
docker compose exec -T db pg_dump -U xiaozhi xiaozhi > backup_$(date +%F).sql
# 恢复
cat backup_2026-01-01.sql | docker compose exec -T db psql -U xiaozhi -d xiaozhi
```

建议加一条每日 cron：

```bash
crontab -e
# 每天 3:30 备份
30 3 * * * cd /opt/xiaozhi-cloud && docker compose exec -T db pg_dump -U xiaozhi xiaozhi > /root/xiaozhi_$(date +\%F).sql
```

---

## 9. 安全加固

```bash
# 只放行 SSH / HTTP / HTTPS
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

- 数据库端口未映射到宿主机，公网无法直连。
- 容器以非 root 用户运行；Caddy 自动管理证书。
- 建议定期 `apt upgrade`，并使用 SSH 密钥登录、关闭密码登录。
- 生产环境 `COOKIE_SECURE=auto` 即可（HTTPS 下自动 Secure）。

---

## 10. 故障排查

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| 页面 502 | app 未起或崩溃 | `docker compose logs app` 看报错 |
| 页面打不开、证书错误 | DNS 未生效或 80/443 未放行 | 检查 A 记录与安全组 |
| 顶部显示「在线模型未配置」 | 没填 API Key 或服务商未启用 | 修改 `.env` 后 `docker compose up -d` |
| 回答报 401 | API Key 错/过期 | 到服务商控制台核对 Key |
| 回答报 429 | 触发限流或模型额度不足 | 稍后重试 / 充值 / 调大 `RATE_LIMIT_PER_MINUTE` |
| 历史丢失 | 用了 `down -v` 或换了数据库卷 | 从备份恢复；勿删 `pgdata` 卷 |
| 中文乱码 | 终端编码问题，非数据问题 | 数据为 UTF-8，浏览器显示正常即可 |

查看容器健康状态：

```bash
docker inspect --format '{{.State.Health.Status}}' $(docker compose ps -q app)
```

---

## 11. 部署完成后

把最终网址发给我，我可以帮你做一轮线上验收（首页、流式对话、多用户隔离、
重启持久化、手机端访问），并把结果写进测试报告。
