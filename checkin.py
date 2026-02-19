"""
audiences.me 自动签到脚本
- 复用已保存的 session（cookies），session 由 setup_session.py 生成
- 点击「签到得爆米花」按钮完成签到
- 遇到 reCAPTCHA 则跳过当天，等待明天重试
"""

import asyncio
import json
import os
import sys
import urllib.request
import urllib.parse
import pyotp
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import stealth_async

BASE_URL = "https://audiences.me"
LOGIN_URL = f"{BASE_URL}/login.php"
CHECKIN_URL = f"{BASE_URL}/attendance.php"
SESSION_FILE = "session.json"

# 从环境变量读取配置
USERNAME = os.environ.get("SITE_USERNAME", "")
PASSWORD = os.environ.get("SITE_PASSWORD", "")
TOTP_SECRET = os.environ.get("TOTP_SECRET", "")
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")


def notify_feishu(title: str, content: str):
    """发送飞书通知，失败时静默忽略不影响主流程"""
    if not FEISHU_WEBHOOK:
        return
    payload = json.dumps({
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": [[{"tag": "text", "text": content}]]
                }
            }
        }
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            FEISHU_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def get_totp_code() -> str:
    """根据 TOTP 密钥生成当前验证码"""
    if not TOTP_SECRET:
        raise ValueError("TOTP_SECRET 环境变量未设置")
    totp = pyotp.TOTP(TOTP_SECRET)
    return totp.now()


async def is_logged_in(page) -> bool:
    """检测当前页面是否已登录"""
    try:
        await page.goto(CHECKIN_URL, wait_until="domcontentloaded", timeout=15000)
        # 如果跳转到登录页，说明未登录
        if "login" in page.url:
            return False
        # 检查页面是否包含签到按钮或已登录特征
        checkin_btn = await page.query_selector("text=签到得爆米花")
        return checkin_btn is not None
    except Exception:
        return False


async def login(page):
    """执行登录 + TOTP 二次验证"""
    print("正在登录...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)

    # 填写账号密码（根据实际页面选择器调整）
    await page.wait_for_selector("input[name='username'], input[type='email'], #username", timeout=10000)

    # 尝试常见的用户名/邮箱选择器
    for selector in ["input[name='username']", "input[name='email']", "#username", "input[type='email']"]:
        el = await page.query_selector(selector)
        if el:
            await el.fill(USERNAME)
            break

    for selector in ["input[name='password']", "#password", "input[type='password']"]:
        el = await page.query_selector(selector)
        if el:
            await el.fill(PASSWORD)
            break

    # 点击登录按钮
    for selector in ["button[type='submit']", "input[type='submit']", "#login-btn", "button:text('登录')", "button:text('Login')"]:
        el = await page.query_selector(selector)
        if el:
            await el.click()
            break

    await asyncio.sleep(2)

    # 检测是否进入 TOTP 验证页面
    totp_input = None
    for selector in ["input[name='otp']", "input[name='twostep']", "input[name='code']", "input[placeholder*='验证']", "input[maxlength='6']"]:
        el = await page.query_selector(selector)
        if el:
            totp_input = el
            break

    if totp_input:
        print("检测到二次验证，正在生成 TOTP 验证码...")
        code = get_totp_code()
        print(f"TOTP 验证码：{code}")
        await totp_input.fill(code)

        # 提交验证码
        for selector in ["button[type='submit']", "input[type='submit']", "button:text('验证')", "button:text('Verify')"]:
            el = await page.query_selector(selector)
            if el:
                await el.click()
                break

        await asyncio.sleep(2)

    # 验证是否登录成功
    if "login" in page.url:
        raise RuntimeError("登录失败，请检查账号密码或 TOTP 密钥")

    print("登录成功！")


async def do_checkin(page) -> str:
    """执行签到，返回结果信息"""
    print("正在前往签到页面...")
    await page.goto(CHECKIN_URL, wait_until="domcontentloaded", timeout=15000)
    await asyncio.sleep(1)

    # 查找签到按钮
    btn = await page.query_selector("text=签到得爆米花")
    if not btn:
        # 备选：查找包含该文字的按钮
        btn = await page.query_selector("a:has-text('签到得爆米花'), button:has-text('签到得爆米花')")

    if not btn:
        # 截图保留现场
        await page.screenshot(path="debug_no_button.png", full_page=True)
        # 检查是否已经签到过
        already = await page.query_selector("text=今日已签到, text=已签到, text=签到成功")
        if already:
            return "今日已签到过，无需重复操作"
        raise RuntimeError("未找到「签到得爆米花」按钮，已截图至 debug_no_button.png")

    await btn.click()
    await asyncio.sleep(2)

    # 检测签到结果（弹窗、提示文字等）
    for selector in [".alert", ".toast", ".message", ".success", "[class*='success']", "[class*='alert']"]:
        el = await page.query_selector(selector)
        if el:
            text = await el.inner_text()
            if text.strip():
                return f"签到结果：{text.strip()}"

    return "签到操作已完成（未检测到明确提示）"


async def main():
    if not USERNAME or not PASSWORD:
        print("错误：请设置 SITE_USERNAME 和 SITE_PASSWORD 环境变量")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # 尝试加载已保存的 session
        context_kwargs = {}
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE) as f:
                context_kwargs["storage_state"] = json.load(f)
            print(f"已加载保存的 session：{SESSION_FILE}")

        context = await browser.new_context(
            **context_kwargs,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        await stealth_async(page)  # 隐藏自动化特征，降低 reCAPTCHA 风险评分

        try:
            # 检测登录状态
            logged_in = await is_logged_in(page)

            if not logged_in:
                print("Session 失效或不存在，重新登录...")
                await login(page)
                # 保存新 session
                storage = await context.storage_state()
                with open(SESSION_FILE, "w") as f:
                    json.dump(storage, f)
                print(f"Session 已保存至 {SESSION_FILE}")
            else:
                print("Session 有效，跳过登录")

            # 执行签到
            result = await do_checkin(page)
            print(result)

        except Exception as e:
            msg = str(e)
            print(f"签到失败：{msg}")
            await page.screenshot(path="error.png", full_page=True)
            notify_feishu(
                title="❌ audiences.me 签到失败",
                content=f"时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n原因：{msg}\n请检查 GitHub Actions 日志或截图。"
            )
            sys.exit(1)

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
