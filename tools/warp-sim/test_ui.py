# -*- coding: utf-8 -*-
"""設定視窗的煙霧測試。

★ 為什麼需要：tkinter 的回呼是執行期才跑，語法檢查與 AST 解析都抓不到
  「元件尚未建立就被回呼引用」這類錯誤。實際發生過：
  on_res() 被安排在 cb_prof 建立之前呼叫 → NameError，程式一啟動就崩潰。
  這支測試把視窗真的建起來（不進 mainloop），並對每個材料觸發一次回呼。
"""
import sys
import tkinter as tk

import app
from materials import RESINS, CURE_PRESETS, recommended_profile, default_shrink_key, CURE_PRESETS_SHRINK

PASS = FAIL = 0
def chk(c, l, d=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  PASS  {l}")
    else: FAIL += 1; print(f"  FAIL  {l}   {d}")

try:
    _probe = tk.Tk(); _probe.destroy()
except Exception as ex:
    print(f"  SKIP  此環境無 GUI，略過（{ex}）")
    sys.exit(0)

print("\n══ 1. 設定視窗可完整建立 ══")
orig_mainloop = tk.Misc.mainloop
tk.Misc.mainloop = lambda self: None       # 不阻塞，只驗證建構過程
try:
    st = app.ask_settings("dummy.stl")
    chk(True, "★ ask_settings 建構完成（含所有回呼初次觸發）")
    chk(st.get("ok") is False, "未按下開始 → ok=False")
except Exception as ex:
    chk(False, "★ ask_settings 建構完成", f"{type(ex).__name__}: {ex}")
finally:
    tk.Misc.mainloop = orig_mainloop

print("\n══ 2. 每個材料的自動帶出都不會出錯 ══")
for name in RESINS:
    try:
        rec = recommended_profile(name)
        key = default_shrink_key(name)
        ok = key in CURE_PRESETS_SHRINK
        chk(ok, f"{name}：收縮預設鍵存在（{key[:22]}…）" if ok else f"{name} 鍵不存在", key)
    except Exception as ex:
        chk(False, f"{name} 自動帶出失敗", ex)

print("\n══ 3. 原廠建議條件的數值合理 ══")
for name in RESINS:
    p = recommended_profile(name)
    if p is None:
        continue
    chk(20 <= p.chamber_temp <= 120 and 1 <= p.duration_min <= 300,
        f"{name}：{p.chamber_temp:.0f}°C / {p.duration_min:.0f}min 在合理範圍",
        (p.chamber_temp, p.duration_min))

print("\n══ 4. 隔水固化的對流係數確實較高 ══")
import materials as M
pw = M.recommended_profile("Silicone 40A")
pa = M.recommended_profile("Clear V5")
if pw:
    chk(pw.h_heat > pa.h_heat * 10,
        f"★ 隔水固化 h={pw.h_heat:.0f} 遠高於空氣 h={pa.h_heat:.0f}")

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)
