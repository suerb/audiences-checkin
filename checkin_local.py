"""
audiences.me 本地签到脚本（Mac 版）
- 复制 Chrome 真实 Profile（含 Turnstile 指纹），绕过 Cloudflare 检测
- browser_cookie3 解密 session cookies 并注入
- 签到结果推送飞书通知
- 配合 macOS launchd 每天定时运行

依赖：
    pip install playwright browser-cookie3
    playwright install chromium
"""

import asyncio
import json
import os
import sys
import shutil
import tempfile
import pathlib
import urllib.request
import datetime
import browser_cookie3
from playwright.async_api import async_playwright

BASE_URL = "https://audiences.me"
CHECKIN_URL = f"{BASE_URL}/attendance.php"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_PROFILE = pathlib.Path.home() / "Library/Application Support/Google/Chrome/Default"

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")


def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def notify_feishu(title: str, content: str, status: str = "success"):
    if not FEISHU_WEBHOOK:
        log("FEISHU_WEBHOOK 未配置，跳过通知")
        return

    header_color = "green" if status == "success" else "red"
    icon = "✅" if status == "success" else "❌"

    payload = json.dumps({
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": header_color,
                "title": {"tag": "plain_text", "content": f"{icon} {title}"}
            },
            "elements": [{
                "tag": "div",
                "text": {"tag": "lark_md", "content": content}
            }]
        }
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            FEISHU_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        log("飞书通知已发送")
    except Exception as e:
        log(f"飞书通知发送失败: {e}")


def get_cookies_for_playwright() -> list:
    """用 browser_cookie3 解密 Chrome cookies，转为 Playwright 格式"""
    log("正在读取 Chrome cookies...")
    jar1 = browser_cookie3.chrome(domain_name="audiences.me")
    jar2 = browser_cookie3.chrome(domain_name=".audiences.me")

    seen = set()
    pw_cookies = []
    for jar in (jar1, jar2):
        for c in jar:
            if c.name in seen:
                continue
            seen.add(c.name)
            cookie = {
                "name": c.name,
                "value": c.value,
                "domain": c.domain if c.domain.startswith(".") else f".{c.domain}",
                "path": c.path or "/",
                "httpOnly": False,
                "secure": bool(c.secure),
                "sameSite": "Lax",
            }
            if c.expires and c.expires > 0:
                cookie["expires"] = float(c.expires)
            pw_cookies.append(cookie)

    if not pw_cookies:
        raise RuntimeError("未读取到任何 cookie，请确保 Chrome 已登录 audiences.me")

    names = [c["name"] for c in pw_cookies]
    log(f"读取到 {len(pw_cookies)} 个 cookies：{names}")
    if "cf_clearance" not in names:
        log("警告：未找到 cf_clearance")
    return pw_cookies


def copy_chrome_profile() -> pathlib.Path:
    """复制 Chrome Default Profile 到临时目录（保留 IndexedDB 等指纹数据）"""
    tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="audiences_checkin_"))
    tmp_user_data = tmp_root / "UserData"
    tmp_default = tmp_user_data / "Default"

    # 只跳过纯缓存目录，保留 IndexedDB（Turnstile 指纹存在这里）
    skip_dirs = {
        "Cache", "Code Cache", "GPUCache", "DawnCache", "ShaderCache",
        "Service Worker", "CacheStorage", "blob_storage",
    }

    def ignore_fn(src, names):
        return [n for n in names if n in skip_dirs]

    log(f"复制 Chrome Profile → {tmp_default}")
    shutil.copytree(str(CHROME_PROFILE), str(tmp_default), ignore=ignore_fn)
    log("Profile 复制完成")
    return tmp_user_data


async def do_checkin(pw_cookies: list) -> str:
    tmp_user_data = copy_chrome_profile()

    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(tmp_user_data),
                executable_path=CHROME_PATH,
                headless=False,
                args=[
                    "--profile-directory=Default",
                    "--window-position=10000,10000",
                    "--window-size=1280,800",
                    "--disable-blink-features=AutomationControlled",
                ],
                viewport={"width": 1280, "height": 800},
            )
            log("已启动 Chrome（复制 Profile）")

            # 注入解密后的 session cookies（覆盖 Profile 里无法解密的加密 cookies）
            await context.add_cookies(pw_cookies)
            log(f"已注入 {len(pw_cookies)} 个解密 cookies")

            page = await context.new_page()

            try:
                log(f"访问 {CHECKIN_URL}")
                await page.goto(CHECKIN_URL, wait_until="networkidle", timeout=45000)
                await asyncio.sleep(3)
                log(f"页面已稳定，当前 URL：{page.url}")

                if "login" in page.url:
                    raise RuntimeError("【Session 失效】被重定向到登录页，请在 Chrome 重新登录 audiences.me")

                page_text = await page.inner_text("body")

                # 已签到
                for kw in ["您今天已经签到过了", "签到已得", "今日已签到"]:
                    if kw in page_text:
                        return f"今日已签到（{kw}）"

                # 找签到按钮
                btn = None
                for selector in ["a:has-text('签到得爆米花')", "button:has-text('签到得爆米花')"]:
                    btn = await page.query_selector(selector)
                    if btn:
                        log(f"找到签到按钮：{selector}")
                        break

                if not btn:
                    await page.screenshot(path="/tmp/audiences_checkin_debug.png", full_page=True)
                    raise RuntimeError("【页面异常】未找到签到按钮，截图已保存至 /tmp/audiences_checkin_debug.png")

                # 等待 Turnstile token 就绪再点击（最多 20 秒）
                log("等待 Turnstile 验证完成...")
                for i in range(20):
                    await asyncio.sleep(1)
                    el = await page.query_selector("input[name='cf-token']")
                    if el:
                        val = await el.get_attribute("value") or ""
                        if val:
                            log(f"Turnstile 已通过（{i+1}秒），开始点击签到")
                            break
                else:
                    log("Turnstile 20 秒内未完成，仍然尝试点击...")

                await btn.click()
                log("已点击签到按钮，等待响应...")
                await asyncio.sleep(10)

                page_text_after = await page.inner_text("body")
                for kw in ["签到已得", "您今天已经签到过了", "今日已签到", "获得爆米花", "签到成功"]:
                    if kw in page_text_after:
                        return f"签到成功（{kw}）"

                btn_after = await page.query_selector("a:has-text('签到得爆米花'), button:has-text('签到得爆米花')")
                if not btn_after:
                    return "签到成功（按钮已消失）"

                await page.screenshot(path="/tmp/audiences_checkin_debug.png", full_page=True)
                raise RuntimeError("【未知结果】点击后未检测到明确结果，截图已保存至 /tmp/audiences_checkin_debug.png")

            finally:
                await context.close()

    finally:
        shutil.rmtree(str(tmp_user_data.parent), ignore_errors=True)
        log("临时 Profile 已清理")


async def main_async():
    log("=== audiences.me 签到开始 ===")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        pw_cookies = get_cookies_for_playwright()
        result = await do_checkin(pw_cookies)
        log(f"签到结果：{result}")
        notify_feishu(
            title="audiences.me 签到成功",
            content=f"时间：{now_str}\n{result}",
            status="success",
        )
        log("=== 签到完成 ===")

    except Exception as e:
        msg = str(e)
        log(f"签到失败：{msg}")
        notify_feishu(
            title="audiences.me 签到失败",
            content=f"时间：{now_str}\n{msg}",
            status="error",
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main_async())
