"""
audiences.me 自动签到脚本
- 复用已保存的 session（cookies），session 由 setup_session.py 生成
- 点击「签到得爆米花」按钮完成签到
- 签到失败时给出明确原因并推送飞书通知
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
        print(f"正在检测登录状态，访问：{CHECKIN_URL}")
        await page.goto(CHECKIN_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)  # 等待 Cloudflare 验证跳转

        current_url = page.url
        title = await page.title()
        print(f"当前页面标题：{title}")
        print(f"当前页面URL：{current_url}")

        if "login" in current_url:
            print("检测到 URL 包含 login，判定为未登录")
            return False

        # 尝试查找签到相关元素
        checkin_btn = await page.query_selector("text=签到得爆米花")
        if checkin_btn:
            return True

        already = await page.query_selector("text=今日已签到, text=签到已得")
        if already:
            return True

        # 如果既不是登录页，也找不到签到按钮，截图看看
        if "login" not in current_url:
            print("未找到签到按钮，但当前不在登录页，判定为已登录（可能是已签到或页面结构变化）")
            return True

        return False
    except PlaywrightTimeout:
        print("检测登录状态超时")
        return False
    except Exception as e:
        print(f"检测登录状态出错：{e}")
        return False


async def login(page):
    """执行登录 + TOTP 二次验证"""
    print("正在登录...")
    try:
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
    except PlaywrightTimeout:
        raise RuntimeError("【网络超时】访问登录页面超时，可能是网站无法访问或正在维护")

    await page.wait_for_selector("input[name='username'], input[type='email'], #username", timeout=10000)

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
        if not TOTP_SECRET:
            raise RuntimeError("【配置缺失】Session 已失效需要重新登录，但 TOTP_SECRET 未配置，无法完成二次验证。请在 GitHub Secrets 中添加 TOTP_SECRET，或重新运行 setup_session.py 生成新的 session.json")
        code = get_totp_code()
        print(f"TOTP 验证码：{code}")
        await totp_input.fill(code)

        for selector in ["button[type='submit']", "input[type='submit']", "button:text('验证')", "button:text('Verify')"]:
            el = await page.query_selector(selector)
            if el:
                await el.click()
                break

        await asyncio.sleep(2)

    if "login" in page.url:
        raise RuntimeError("【登录失败】账号密码错误，或 TOTP 验证码已过期。请检查 SITE_USERNAME / SITE_PASSWORD / TOTP_SECRET 是否正确")

    print("登录成功！")


async def do_checkin(page) -> str:
    """执行签到，返回结果信息"""
    print("正在前往签到页面...")
    try:
        await page.goto(CHECKIN_URL, wait_until="domcontentloaded", timeout=15000)
    except PlaywrightTimeout:
        raise RuntimeError("【网络超时】加载签到页面超时，可能是网站正在维护或 GitHub Actions IP 被临时限流")

    await asyncio.sleep(1)

    # 检测 reCAPTCHA
    captcha = await page.query_selector("iframe[src*='recaptcha'], .g-recaptcha, #recaptcha")
    if captcha:
        await page.screenshot(path="debug_captcha.png", full_page=True)
        raise RuntimeError("【reCAPTCHA 拦截】签到页面触发了人机验证，stealth 模式本次未能绕过。截图已上传至 Artifacts，明天将自动重试")

    # 查找签到按钮
    btn = await page.query_selector("text=签到得爆米花")
    if not btn:
        btn = await page.query_selector("a:has-text('签到得爆米花'), button:has-text('签到得爆米花')")

    if not btn:
        await page.screenshot(path="debug_no_button.png", full_page=True)
        # 检查是否已经签到过
        already = await page.query_selector("text=今日已签到, text=已签到, text=签到成功, text=签到已得")
        if already:
            return "今日已签到过，无需重复操作"
        # 检查是否名额已满
        full = await page.query_selector("text=名额已满, text=已满, text=今日名额")
        if full:
            raise RuntimeError("【名额已满】今日签到名额已被抢完，明天 00:00 刷新后将自动重试")
        raise RuntimeError("【页面异常】未找到「签到得爆米花」按钮，可能是网站改版或页面结构变化。截图已上传至 Artifacts，请人工检查")

    await btn.click()
    await asyncio.sleep(2)

    # 检测签到结果
    for selector in [".alert", ".toast", ".message", ".success", "[class*='success']", "[class*='alert']"]:
        el = await page.query_selector(selector)
        if el:
            text = await el.inner_text()
            if text.strip():
                return f"签到结果：{text.strip()}"

    return "签到操作已完成（未检测到明确提示）"


async def main():
    if not USERNAME or not PASSWORD:
        msg = "【配置缺失】GitHub Secrets 中未找到 SITE_USERNAME 或 SITE_PASSWORD，请检查仓库 Settings → Secrets 配置"
        print(msg)
        notify_feishu(title="❌ audiences.me 签到失败", content=f"时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n{msg}")
        sys.exit(1)

    if not os.path.exists(SESSION_FILE):
        msg = "【配置缺失】未找到 session.json，请先运行 setup_session.py 完成首次登录，并将 base64 内容存为 GitHub Secret SESSION_JSON，同时在 workflow 中加入还原步骤"
        print(msg)
        notify_feishu(title="❌ audiences.me 签到失败", content=f"时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n{msg}")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        with open(SESSION_FILE) as f:
            context_kwargs = {"storage_state": json.load(f)}
        print(f"已加载保存的 session：{SESSION_FILE}")

        context = await browser.new_context(
            **context_kwargs,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        await stealth_async(page)

        try:
            logged_in = await is_logged_in(page)

            if not logged_in:
                print("Session 失效，重新登录...")
                await login(page)
                storage = await context.storage_state()
                with open(SESSION_FILE, "w") as f:
                    json.dump(storage, f)
                print(f"Session 已保存至 {SESSION_FILE}")
            else:
                print("Session 有效，跳过登录")

            result = await do_checkin(page)
            print(result)
            notify_feishu(
                title="✅ audiences.me 签到成功",
                content=f"时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n{result}"
            )

        except Exception as e:
            msg = str(e)
            print(f"签到失败：{msg}")
            await page.screenshot(path="error.png", full_page=True)
            notify_feishu(
                title="❌ audiences.me 签到失败",
                content=f"时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n{msg}"
            )
            sys.exit(1)

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
