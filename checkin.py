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


def notify_feishu(title: str, content: str, status: str = "success"):
    """发送飞书富文本卡片通知
    status: success / error
    """
    if not FEISHU_WEBHOOK:
        return

    # 拼接 GitHub Actions 运行链接
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"{server_url}/{repo}/actions/runs/{run_id}" if run_id else "https://github.com/suerb/audiences-checkin/actions"

    # 根据状态设置颜色和图标
    header_color = "green" if status == "success" else "red"
    icon = "✅" if status == "success" else "❌"

    payload = json.dumps({
        "msg_type": "interactive",
        "card": {
            "config": {
                "wide_screen_mode": True
            },
            "header": {
                "template": header_color,
                "title": {
                    "tag": "plain_text",
                    "content": f"{icon} {title}"
                }
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": content
                    }
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": "查看运行详情 & 截图"
                            },
                            "type": "primary",
                            "url": run_url
                        }
                    ]
                }
            ]
        }
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            FEISHU_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"飞书通知发送失败: {e}")


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
    print("已点击签到按钮，等待 5 秒响应...")
    await asyncio.sleep(5)

    # 截图记录点击后的状态
    await page.screenshot(path="debug_after_click.png", full_page=True)

    # 再次检测 reCAPTCHA (点击后触发)
    captcha = await page.query_selector("iframe[src*='recaptcha'], .g-recaptcha, #recaptcha")
    if captcha:
        raise RuntimeError("【签到中断】点击按钮后触发了 reCAPTCHA 人机验证，导致签到未完成。截图已上传。")

    # 检测签到结果 - 仅匹配精准的提示框
    for selector in [".alert", ".toast", ".message", ".success", "[class*='success']", "[class*='alert']"]:
        el = await page.query_selector(selector)
        if el:
            text = await el.inner_text()
            text = text.strip()
            if text:
                 return f"签到结果：{text}"

    # 如果没有弹窗，检查是否有具体的文字变化
    success_text = await page.query_selector("text=今日已签到, text=签到已得, text=获得, text=您今天已经签到过了")
    if success_text:
        return f"签到结果：{await success_text.inner_text()}"

    # 再次检查按钮状态：如果原来的按钮找不到了，或者文字变了，说明点击生效了
    btn_after = await page.query_selector("text=签到得爆米花")
    if not btn_after:
        return "签到操作已执行（按钮已消失，判定为成功）"

    # 或者按钮还在，但文字变了
    btn_text = await btn_after.inner_text()
    if "签到" not in btn_text or "已" in btn_text:
         return f"签到操作已执行（按钮文字变为：{btn_text}）"

    # 如果运行到这里，说明按钮还在，也没变，也没提示
    # 强制抛出异常，以便上传截图供人工排查
    raise RuntimeError("【未知结果】点击签到后未检测到明确的成功/失败提示。请查看 Artifacts 中的 debug_after_click.png 确认页面状态。")


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
