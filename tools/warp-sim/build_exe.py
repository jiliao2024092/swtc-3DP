# -*- coding: utf-8 -*-
"""打包成 Windows 執行檔。

跑法（在 tools/warp-sim 目錄下）：
    venv\\Scripts\\python.exe build_exe.py              → onedir（預設，啟動快）
    venv\\Scripts\\python.exe build_exe.py --onefile    → 單一 exe（好散佈，啟動慢）

兩種格式的取捨：
  onedir   dist/SLA後固化變形模擬/ 資料夾（約 484 MB），啟動數秒。
           要整個資料夾一起複製才能執行。
  onefile  單一 exe（約 250–350 MB）。**每次啟動都要把內容解壓到暫存目錄**，
           首次啟動可能等 30–60 秒，且較容易被防毒軟體誤判攔截。
           好處是只有一個檔案，方便用 email／隨身碟給別人。
"""
import subprocess
import sys
import pathlib

HERE = pathlib.Path(__file__).parent
ONEFILE = "--onefile" in sys.argv

args = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--name", "SLA後固化變形模擬" + ("_單檔" if ONEFILE else ""),
    "--onefile" if ONEFILE else "--onedir",
    "--windowed",                       # 不開主控台視窗
    # tetgen 是 C++ 擴充，pyvista/VTK 帶大量原生 DLL，都要 collect-all
    "--collect-all", "tetgen",
    "--collect-all", "pyvista",
    "--collect-all", "vtkmodules",
    "--collect-data", "vtk",
    # scipy 的稀疏求解器有不少隱式匯入
    "--hidden-import", "scipy.sparse.linalg",
    "--hidden-import", "scipy.spatial",
    "--hidden-import", "tkinter",
    str(HERE / "app.py"),
]

print("執行：\n  " + " ".join(args) + "\n")
raise SystemExit(subprocess.call(args, cwd=str(HERE)))
