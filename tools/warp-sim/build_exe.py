# -*- coding: utf-8 -*-
"""打包成單一資料夾的 Windows 執行檔。

跑法（在 tools/warp-sim 目錄下）：
    venv\\Scripts\\python.exe build_exe.py

產出：dist/SLA後固化變形模擬/SLA後固化變形模擬.exe

★ 用 onedir 而非 onefile：
  VTK 與 gmsh 都帶大量原生 DLL，onefile 每次啟動要解壓到暫存目錄，
  啟動時間長達數十秒且偶爾被防毒誤攔。onedir 啟動快、也好排查缺檔問題。
"""
import subprocess
import sys
import pathlib

HERE = pathlib.Path(__file__).parent

args = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--name", "SLA後固化變形模擬",
    "--windowed",                       # 不開主控台視窗
    # gmsh 的 Python wrapper 靠 ctypes 載入原生 DLL，PyInstaller 抓不到，
    # 必須手動指定 collect-all
    "--collect-all", "gmsh",
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
