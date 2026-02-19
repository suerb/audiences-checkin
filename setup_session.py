"""
首次运行此脚本来完成登录并保存 session。
之后 checkin.py 会自动复用保存的 session，无需重复登录。

使用方式：
    python setup_session.py
"""

import asyncio
import json
import os
import pyotp
from playwright.async_api import async_playwright

BASE_URL = "https://audiences.me"
LOGIN_URL = f"{BASE_URL}/login.php"
SESSION_FILE = "session.json"


async def setup():
    username = input("请输入用户名/邮箱：").strip()
    password = input("请输入密码：").strip()
    totp_secret = input("请输入 Google Authenticator 密钥（Base32，非验证码）：").strip()

    async with async_playwright() as p:
        # 首次登录用有界面模式，方便观察
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        print("\n正在打开登录页面...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # 填写用户名
        for selector in ["input[name='username']", "input[name='email']", "#username", "input[type='email']"]:
            el = await page.query_selector(selector)
            if el:
                await el.fill(username)
                print(f"已填写用户名（选择器：{selector}）")
                break

        # 填写密码
        for selector in ["input[name='password']", "#password", "input[type='password']"]:
            el = await page.query_selector(selector)
            if el:
                await el.fill(password)
                print("已填写密码")
                break

        # 提交登录表单
        for selector in ["button[type='submit']", "input[type='submit']", "#login-btn", "button:text('登录')", "button:text('Login')"]:
            el = await page.query_selector(selector)
            if el:
                await el.click()
                print("已点击登录按钮")
                break

        await asyncio.sleep(2)

        # 处理 TOTP 二次验证
        totp_input = None
        for selector in ["input[name='otp']", "input[name='twostep']", "input[name='code']", "input[maxlength='6']"]:
            el = await page.query_selector(selector)
            if el:
                totp_input = el
                break

        if totp_input:
            code = pyotp.TOTP(totp_secret).now()
            print(f"正在输入 TOTP 验证码：{code}")
            await totp_input.fill(code)

            for selector in ["button[type='submit']", "input[type='submit']", "button:text('验证')", "button:text('Verify')"]:
                el = await page.query_selector(selector)
                if el:
                    await el.click()
                    break

            await asyncio.sleep(2)
        else:
            print("未检测到 TOTP 验证页，如果浏览器弹出了验证页请手动完成后按回车继续...")
            input("完成后按回车...")

        # 保存 session
        storage = await context.storage_state()
        with open(SESSION_FILE, "w") as f:
            json.dump(storage, f)

        print(f"\nSession 已保存至 {SESSION_FILE}")
        print("现在可以运行 checkin.py 进行自动签到了")

        # 打印环境变量提示
        print("\n--- 请将以下内容设置为 GitHub Actions Secrets ---")
        print(f"SITE_USERNAME={username}")
        print(f"SITE_PASSWORD={password}")
        print(f"TOTP_SECRET={totp_secret}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(setup())
