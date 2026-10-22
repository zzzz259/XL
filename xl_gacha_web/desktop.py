"""Portable Windows launcher: loopback service, disk archive, default browser."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import threading
import time
import webbrowser
from pathlib import Path

from app import APP_HOME, WebServer


def load_instance(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        port = value.get("port")
        if type(port) is int and 1 <= port <= 65535:
            return f"http://127.0.0.1:{port}/"
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def make_server(directory, port=8788):
    try:
        return WebServer(("127.0.0.1", port), archive_dir=directory)
    except OSError:
        if port == 0:
            raise
        return WebServer(("127.0.0.1", 0), archive_dir=directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=APP_HOME / "user-data")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the browser",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without the launcher control window",
    )
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    mutex = None
    kernel = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        instance = directory / "instance.json"
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_bool,
                ctypes.c_wchar_p,
            ]
            kernel.CreateMutexW.restype = ctypes.c_void_p
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            name = (
                "Local\\StarfallGacha-"
                + hashlib.sha256(str(directory).lower().encode()).hexdigest()[:32]
            )
            mutex = kernel.CreateMutexW(None, False, name)
            if not mutex:
                raise OSError("instance lock unavailable")
            if ctypes.get_last_error() == 183:
                for _ in range(100):
                    url = load_instance(instance)
                    if url:
                        if not args.no_browser:
                            webbrowser.open(url)
                        return
                    time.sleep(0.1)
                raise OSError("existing instance not ready")
        server = make_server(directory, args.port)
        url = f"http://127.0.0.1:{server.server_port}/"
        temp = instance.with_suffix(".tmp")
        temp.write_text(
            json.dumps({"pid": os.getpid(), "port": server.server_port}),
            encoding="utf-8",
        )
        temp.replace(instance)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server.environment.start()
        server.catalog_store.start()
        try:
            if not args.no_browser:
                webbrowser.open(url)
            if args.headless:
                while thread.is_alive():
                    thread.join(timeout=1)
            else:
                control_window(url, directory, server.environment)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            instance.unlink(missing_ok=True)
    except KeyboardInterrupt:
        pass
    except Exception:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
        # No arbitrary exception strings or network data in crash dialogs/files.
        if os.name == "nt" and not args.headless:
            ctypes.windll.user32.MessageBoxW(
                None,
                "程序未能启动。请完整解压到可写目录，并检查 user-data 文件夹权限。\n"
                "若已有程序在运行，请使用原来的启动窗口。",
                "星落 · 抽卡手记",
                0x10,
            )
        else:
            raise SystemExit(
                "Local app could not start; check archive directory and loopback access."
            ) from None
    finally:
        if mutex and kernel:
            kernel.CloseHandle(mutex)


def control_window(url, directory, environment=None):
    import tkinter as tk
    from tkinter import ttk

    window = tk.Tk()
    window.title("星落 · 抽卡手记")
    window.geometry("580x390")
    window.resizable(False, False)
    window.configure(bg="#17212d")
    try:
        from app import ROOT

        window.iconbitmap(str(ROOT / "data" / "app.ico"))
    except tk.TclError:
        pass
    tk.Label(
        window,
        text="✦ 星落 · 抽卡手记",
        font=("Microsoft YaHei UI", 20, "bold"),
        bg="#17212d",
        fg="#f0dfbd",
    ).pack(pady=(24, 10))
    tk.Label(
        window,
        text="本机服务已启动，请在浏览器中操作。",
        font=("Microsoft YaHei UI", 11),
        bg="#17212d",
        fg="#e3e9ef",
    ).pack()
    entry = tk.Entry(
        window,
        justify="center",
        font=("Consolas", 12),
        width=42,
        readonlybackground="#202d3d",
        fg="#dfc58f",
        relief="flat",
    )
    entry.insert(0, url)
    entry.configure(state="readonly")
    entry.pack(pady=14, ipady=6)
    buttons = tk.Frame(window, bg="#17212d")
    buttons.pack(pady=3)
    ttk.Button(buttons, text="打开浏览器", command=lambda: webbrowser.open(url)).pack(
        side="left", padx=6
    )
    ttk.Button(
        buttons, text="打开存档文件夹", command=lambda: os.startfile(directory)
    ).pack(side="left", padx=6)
    ttk.Button(buttons, text="退出程序", command=window.destroy).pack(
        side="left", padx=6
    )
    tk.Label(
        window,
        text="记录保存到程序旁的 user-data 文件夹。\n关闭浏览器不会退出程序，关闭此窗口会停止本机服务。",
        font=("Microsoft YaHei UI", 10),
        bg="#17212d",
        fg="#a9b7c9",
        justify="center",
    ).pack(pady=18)
    check_label = tk.Label(
        window,
        text="启动环境检查中…",
        font=("Microsoft YaHei UI", 10),
        bg="#17212d",
        fg="#a9b7c9",
        wraplength=530,
    )
    check_label.pack()

    def refresh_checks():
        if not environment:
            return
        result = environment.snapshot()
        if result["phase"] == "done":
            problems = [
                {
                    "browser": "登录浏览器未找到",
                    "directory": "存档目录不可写",
                    "loopback": "本机回环连接不可用",
                    "official_login": "官网登录连接异常",
                    "official_auth": "游戏认证连接异常",
                }[c["name"]]
                for c in result["checks"]
                if c["status"] != "ok"
            ]
            check_label.config(
                text="；".join(problems) + "。请在网页“浏览器与环境检查”查看详情。"
                if problems
                else "启动环境检查通过 · 查询直接连接官方服务",
                fg="#e6bd82" if problems else "#8fc6b1",
            )
        else:
            window.after(500, refresh_checks)

    refresh_checks()
    window.mainloop()


if __name__ == "__main__":
    main()
