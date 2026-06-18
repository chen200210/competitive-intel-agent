"""
点点数据 - 登录态保存。

打开 Chrome → 手动登录点点数据 → 按 Enter → 登录态保存到共享目录。
与 OA 项目和 DOSH 项目共享同一份登录态。

用法:
    # 在 OA 的 venv 环境下
    python tools/diandian_auth.py
"""

from __future__ import annotations

from pathlib import Path
from playwright.sync_api import sync_playwright

# Chrome Profile 目录（OA 项目自有）
CHROME_PROFILE = Path(__file__).resolve().parent.parent / "data" / ".diandian_chrome_profile"


def main() -> None:
    print("=" * 50)
    print("🔐 点点数据 - 登录态保存")
    print(f"   配置目录: {CHROME_PROFILE}")
    print("=" * 50)
    print()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE),
            headless=False,
            accept_downloads=True,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)

        page = context.new_page()
        page.goto("https://app.diandian.com/login")

        print("👆 请在浏览器中登录点点数据。")
        print("   登录成功后 → 回到这里按 Enter")
        print()

        input("按 Enter 保存登录态...")

        context.storage_state(path=str(CHROME_PROFILE / "auth.json"))
        context.close()

    print(f"\n✅ 登录态已保存")
    print(f"   下次运行 diandian_scroll.py 会自动复用")


if __name__ == "__main__":
    main()
