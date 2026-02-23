# audiences.me 自动签到（Mac 本地方案）

每天北京时间 03:00 自动签到，结果推送飞书通知，零维护。

---

## 方案原理

```
每天 03:00
  macOS launchd 触发 run_checkin.sh
    → checkin_local.py
        → browser_cookie3 读取 Chrome 最新 cookies（含 cf_clearance）
        → 复制 Chrome 真实 Profile（含 Turnstile 指纹数据）
        → Playwright 用真实 Chrome 启动
        → 注入解密后的 session cookies
        → 访问签到页面，Cloudflare Turnstile 自动通过
        → 点击签到按钮
        → 结果推送飞书
```

**为什么这样做：**

| 问题 | 解法 |
|---|---|
| Cloudflare Turnstile 拦截自动化浏览器 | 复制真实 Chrome Profile（含 IndexedDB 指纹），Turnstile 无法区分真实用户 |
| cf_clearance 随时过期 | browser_cookie3 实时读取 Chrome Cookie 数据库，每次运行都是最新 cookie |
| GitHub Actions IP 可疑 | 本机运行，IP 与日常浏览完全一致 |

---

## 文件说明

| 文件 | 用途 |
|---|---|
| `checkin_local.py` | 核心签到逻辑 |
| `run_checkin.sh` | launchd 调用的启动脚本（加载 .env、写日志） |
| `install_launchd.sh` | 一键安装定时任务 |
| `me.audiences.checkin.plist` | launchd plist 模板 |
| `requirements_local.txt` | Python 依赖 |
| `.env.example` | 配置文件模板（提交 Git，供参考） |
| `.env` | 配置文件（不提交 Git，含飞书 Webhook） |
| `checkin.log` | 运行日志（不提交 Git） |

---

## 安装

### 前提

- macOS，Mac 不关机
- Chrome 已登录 audiences.me
- Python 3.9+

### 步骤

**1. 配置飞书 Webhook（可选，用于推送签到结果）**

```bash
# 复制配置模板
cp .env.example .env

# 编辑配置文件，填入你的飞书 Webhook
nano .env
```

在 `.env` 文件中填入：
```
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/你的webhook地址
```

**2. 一键安装**

```bash
bash install_launchd.sh
```

自动完成：创建 venv → 安装依赖 → 注册 launchd 定时任务（每天北京时间 03:00）。

**3. 立即测试**

```bash
bash run_checkin.sh
```

日志出现 `签到完成` 或 `今日已签到` 即为正常。

---

## 日常操作

| 操作 | 命令 |
|---|---|
| 查看日志 | `tail -50 checkin.log` |
| 手动触发签到 | `bash run_checkin.sh` |
| 查看定时任务状态 | `launchctl list \| grep audiences` |
| 卸载定时任务 | `launchctl unload ~/Library/LaunchAgents/me.audiences.checkin.plist` |

---

## 故障处理

**飞书收到失败通知**
在 Chrome 里打开 audiences.me 随便逛一下，等第二天自动重试。大概率是 cf_clearance 过期，Chrome 正常访问后会自动刷新。

**"Session 失效"错误**
在 Chrome 里重新登录 audiences.me。

---

## 维护成本

几乎为零。只要 Mac 不关机、Chrome 偶尔正常访问 audiences.me（每周一次即可），系统永远自动运行。
