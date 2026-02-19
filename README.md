# audiences.me 自动签到

每天凌晨自动在 [audiences.me](https://audiences.me) 完成签到，基于 GitHub Actions 云端运行，无需本地开机。

---

## 需求背景

- 目标网站：`https://audiences.me/attendance.php`
- 签到按钮：「签到得爆米花」，每天 00:00 刷新名额
- 登录方式：账号密码 + Google Authenticator 二次验证（仅首次登录需要）
- 签到页存在 reCAPTCHA v2（不一定每次触发）
- 要求连续签到不中断
- 签到失败时通过飞书机器人发送通知

---

## 系统架构

```
audiences-checkin/
├── checkin.py                # 核心：每日签到逻辑
├── setup_session.py          # 一次性工具：首次登录，保存登录态
├── requirements.txt          # Python 依赖
├── .gitignore                # 排除 session.json、截图等敏感/临时文件
└── .github/workflows/
    └── checkin.yml           # GitHub Actions 定时调度
```

### 运行流程

```
首次使用（本地）
──────────────────────────────────────────
python setup_session.py
  └─ 弹出浏览器（有界面）
  └─ 填账号密码 → 手动输入 TOTP 验证码 → 登录成功
  └─ 保存 session.json
  └─ 将 session.json base64 编码 → 存为 GitHub Secret SESSION_JSON


每日自动（GitHub Actions 云端）
──────────────────────────────────────────
UTC 16:05 = 北京时间 00:05 触发
  └─ 还原 session.json（从 Secret 解码）
  └─ checkin.py 启动
       ├─ playwright-stealth 隐藏自动化特征（降低 reCAPTCHA 风险评分）
       ├─ 加载 session.json，检测登录态
       │    └─ session 失效时 → 用 TOTP_SECRET 自动重新登录
       ├─ 点击「签到得爆米花」
       ├─ 成功 → 打印结果，结束
       └─ 失败 → 截图上传 Artifacts + 飞书通知


每月保活（GitHub Actions 云端）
──────────────────────────────────────────
每月 1 日 / 20 日触发
  └─ git commit --allow-empty（防止 GitHub 60 天无活动暂停 Actions）
```

### 关键技术决策

| 问题 | 方案 |
|---|---|
| TOTP 二次验证 | `setup_session.py` 手动输入一次，之后复用 session，正常不再触发 |
| session 云端存储 | base64 编码存为 GitHub Secret，不提交进 Git |
| reCAPTCHA v2 | `playwright-stealth` 降低风险评分，大概率自动通过无需人工干预 |
| 签到失败感知 | 飞书 Webhook 通知 + GitHub Artifacts 截图存档 |
| 仓库活跃保活 | 每月 1、20 日空提交，避免 GitHub 60 天后暂停定时任务 |

---

## 使用说明

### 前置条件

- Python 3.10+
- GitHub 账号
- 飞书群机器人 Webhook（可选，用于失败通知）

---

### 第一步：配置 GitHub Secrets

仓库 → Settings → Secrets and variables → Actions → New repository secret，添加以下 Secrets：

| Secret 名称 | 内容 | 必填 |
|---|---|---|
| `SITE_USERNAME` | 网站账号 | ✅ |
| `SITE_PASSWORD` | 网站密码 | ✅ |
| `FEISHU_WEBHOOK` | 飞书机器人 Webhook URL | 可选 |
| `SESSION_JSON` | session.json 的 base64 内容 | ✅（第三步完成后添加）|

> 飞书机器人创建方式：飞书任意群 → 设置 → 机器人 → 添加机器人 → 自定义机器人 → 复制 Webhook URL

---

### 第二步：本地安装依赖

```bash
cd audiences-checkin
pip install -r requirements.txt
playwright install chromium
```

---

### 第三步：首次登录，生成 session

```bash
python setup_session.py
```

脚本会弹出浏览器，按提示输入账号、密码，并手动输入 Google Authenticator 当前验证码，完成登录后自动保存 `session.json`。

然后将其 base64 编码并存为 GitHub Secret：

```bash
base64 -i session.json
# 复制输出内容，存为 Secret：SESSION_JSON
```

---

### 第四步：补充 SESSION_JSON 还原步骤

在 `.github/workflows/checkin.yml` 的 checkin job 中，`检出代码` 步骤之后加入：

```yaml
- name: 还原 session 文件
  run: echo "${{ secrets.SESSION_JSON }}" | base64 -d > session.json
```

---

### 第五步：手动触发，验证云端是否跑通

仓库 → Actions → 「每日自动签到」→ Run workflow

查看运行日志，确认签到成功。

---

## 签到失败通知格式

配置飞书 Webhook 后，失败时会收到如下消息：

```
❌ audiences.me 签到失败
时间：2026-02-20 00:05
原因：未找到「签到得爆米花」按钮
请检查 GitHub Actions 日志或截图。
```

失败截图会上传至 GitHub Actions → 对应 workflow run → Artifacts，保留 3 天。

---

## 待确认事项

- [ ] 签到请求后端是否校验 `g-recaptcha-response`（明天签到时用 DevTools → Network 抓包确认）
  - 不校验 → 问题彻底解决，stealth 方案足够
  - 校验 → 考虑接入音频识别方案（Whisper 本地免费识别）
