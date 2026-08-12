# -*- coding: utf-8 -*-
"""瀏覽器介面版進入點：pywebview 內嵌視窗 + 本機檔案，不開任何 port。

跑法（在 tools/warp-sim 目錄下）：
    venv\\Scripts\\python.exe app_web.py [模型.stl]

── 為什麼是 pywebview 而不是「開瀏覽器連 localhost」───────────
pywebview 的 js_api 讓 JS 直接呼叫 Python 方法，**不需要 HTTP 伺服器、
不綁 port**：不會跳防火牆提示、不會有 port 被占用的問題，
使用者看到的也是一般桌面視窗而不是帶網址列的瀏覽器分頁。

── 與 app.py（tkinter + VTK 版）的關係 ────────────────────
求解核心（fea / mechanics / meshing / materials）兩邊共用，一行都沒改。
app.py 仍然可用，兩者可並存到瀏覽器版驗證完畢為止。
"""
import os
import sys
import pathlib

import webview

from webapi import Api

HERE = pathlib.Path(__file__).parent
WEBUI = HERE / "webui"


def _entry():
    """webui/index.html 的絕對路徑。PyInstaller 打包後改由 _MEIPASS 取得。"""
    base = pathlib.Path(getattr(sys, "_MEIPASS", HERE))
    p = base / "webui" / "index.html"
    if not p.exists():                       # 原始碼執行
        p = WEBUI / "index.html"
    return str(p)


def main():
    entry = _entry()
    if not os.path.exists(entry):
        print(f"[錯誤] 找不到介面檔案：{entry}")
        return 1

    api = Api()
    window = webview.create_window(
        "SLA 後固化變形模擬",
        url=entry,
        js_api=api,
        width=1440, height=900, min_size=(1100, 700),
        text_select=True,
    )
    # Api 需要 window 才能開系統原生的檔案選擇對話框。
    # ⚠ 一定要存進**底線開頭**的屬性：pywebview 會把 Api 的公開屬性
    #   一併暴露給 JS 並嘗試序列化，而序列化 Window 會去讀 WebView2 的
    #   COM 屬性——那些只能在 UI 執行緒存取，在橋接執行緒讀就直接炸
    #   （症狀是「一點就當掉」，實際踩過）。
    api._window = window

    # gui=None 讓 pywebview 自己挑；Windows 上會用 EdgeChromium (WebView2)
    webview.start(debug=("--debug" in sys.argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
