# -*- coding: utf-8 -*-
"""tools/test_functions_wiring.py — functions/main.py 的「部署接線」測試

為什麼需要（2026-09-04 實際事故）：
    commit 9c797bc 把 backfill_ef_no_only 插進「@https_fn.on_call 裝飾器」與
    sync_formlabs_manual 之間，裝飾器就被它接走了。後果有三個，而且全都是靜默的：
      1. sync_formlabs_manual 整支沒有被部署（firebase functions:list 裡根本沒有它）
      2. backfill_ef_no_only 被當成 callable 部署，卻沒有 req 參數 → 一呼叫就 TypeError
      3. auth 檢查寫在 sync_formlabs_manual 裡，等於那個 endpoint 沒有任何身分驗證

    ⚠ 這個 bug 潛伏了好幾週，既有的 592 項測試沒有一項抓得到 ——
    它們測的是純函式的邏輯，不管「哪幾支會被部署、簽章對不對」。
    語法檢查（py_compile）也看不出來，因為程式碼完全合法。
    唯一能看出來的是解析 AST 比對裝飾器歸屬，或者去打 firebase functions:list。

這支測試用 AST 釘住三件事：
  1. 會被部署的 function 名單，逐字等於預期（多一支、少一支都 FAIL）
  2. 每支的簽章與觸發類型相符（on_call 收 req、on_schedule 收 event）
  3. 每支 on_call 都有自己的 auth 檢查（不可只靠別支代勞）
  4. 內部 helper 不可被裝飾（被裝飾就不能再用一般方式呼叫）

執行：python tools/test_functions_wiring.py     （於 repo 根目錄）
"""
import ast
import io
import os
import sys

# Windows 主控台是 cp950，print 中文會 UnicodeEncodeError（跟其他測試同一個處理）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "functions", "main.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)

_pass = 0
_fail = 0


def eq(actual, expected, why):
    global _pass, _fail
    if actual == expected:
        _pass += 1
    else:
        _fail += 1
        print(f"FAIL  {why}\n      實際 {actual!r} / 預期 {expected!r}")


# ── 掃出所有頂層函式與它們的裝飾器 ────────────────────────────────────
def _dec_name(d):
    """把裝飾器節點還原成 'https_fn.on_call' 這種可比對的字串。"""
    f = d.func if isinstance(d, ast.Call) else d
    return ast.unparse(f)


def _dec_kwargs(d):
    """取裝飾器的關鍵字參數（region / memory / secrets…）。"""
    if not isinstance(d, ast.Call):
        return {}
    out = {}
    for kw in d.keywords:
        if kw.arg is None:
            continue
        try:
            out[kw.arg] = ast.literal_eval(kw.value)
        except Exception:
            out[kw.arg] = ast.unparse(kw.value)
    return out


FUNCS = {}       # name -> ast node（所有頂層 def）
DECORATED = {}   # name -> [裝飾器字串]
for node in TREE.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        FUNCS[node.name] = node
        decs = [_dec_name(d) for d in node.decorator_list]
        if decs:
            DECORATED[node.name] = decs


# ── 1. 部署名單逐字比對 ───────────────────────────────────────────────
# ★ 這份名單就是 `firebase functions:list --project swtc-3dp-poc` 應該看到的內容。
#   要新增/移除 Cloud Function 時，這裡要跟著改 —— 這個 FAIL 是刻意的提醒，不是誤報。
EXPECTED_DEPLOYED = {
    "sync_formlabs_scheduled": "scheduler_fn.on_schedule",
    "sync_formlabs_manual":    "https_fn.on_call",
    "sync_eiger_scheduled":    "scheduler_fn.on_schedule",
    "sync_eiger_manual":       "https_fn.on_call",
}

eq(sorted(DECORATED.keys()), sorted(EXPECTED_DEPLOYED.keys()),
   "★ 會被部署的 function 名單要跟預期一致（少一支＝那支沒部署且毫無錯誤訊息）")

for name, trigger in EXPECTED_DEPLOYED.items():
    eq(name in DECORATED, True, f"{name} 必須有裝飾器（沒有就不會被部署）")
    eq(DECORATED.get(name, []), [trigger],
       f"{name} 的觸發類型要是 {trigger}，且只掛這一個裝飾器")


# ── 2. 簽章與觸發類型相符 ─────────────────────────────────────────────
# on_call 一定會傳 CallableRequest 進來、on_schedule 一定會傳 ScheduledEvent 進來。
# 簽章不收就是「部署得起來、一呼叫就 TypeError」——正是 2026-09-04 那次的症狀。
for name, trigger in EXPECTED_DEPLOYED.items():
    node = FUNCS.get(name)
    if node is None:
        eq(False, True, f"main.py 找不到 {name}")
        continue
    args = [a.arg for a in node.args.args]
    want = ["req"] if trigger == "https_fn.on_call" else ["event"]
    eq(args, want,
       f"★ {name} 的參數要是 {want}（{trigger} 一定會傳一個參數進來，不收就必 TypeError）")


# ── 3. 每支 on_call 都要有自己的 auth 檢查 ────────────────────────────
# ⚠ Firebase v2 callable 預設允許未驗證身分呼叫，擋不擋人全看 handler 自己。
#   auth 檢查不可以「寫在另一支函式裡」——裝飾器一旦掛錯支，那道檢查就跟著失效。
for name, trigger in EXPECTED_DEPLOYED.items():
    if trigger != "https_fn.on_call":
        continue
    body_src = ast.unparse(FUNCS[name])
    eq("req.auth" in body_src, True,
       f"★ {name} 必須自己檢查 req.auth（callable 預設對外開放，沒檢查＝任何人可呼叫）")
    eq("PERMISSION_DENIED" in body_src or "permission_denied" in body_src.lower(), True,
       f"★ {name} 必須擋掉非 admin（只驗登入不夠）")


# ── 4. 內部 helper 不可被裝飾 ─────────────────────────────────────────
# 被 on_call 裝飾之後，函式就變成收 CallableRequest 的 handler，
# 原本 `return backfill_ef_no_only()` 這種一般呼叫會當場壞掉。
# ⚠ 名字打錯就等於這道守衛消失，所以「找不到」本身要 FAIL，不可靜默跳過
#   （初版寫成 perform_eiger_sync，實際叫 perform_sync_eiger，那一項等於沒測）。
INTERNAL_HELPERS = ["backfill_ef_no_only", "perform_sync", "perform_sync_eiger"]
for name in INTERNAL_HELPERS:
    eq(name in FUNCS, True,
       f"★ main.py 要找得到內部 helper {name}（找不到通常是改名了，這道守衛會跟著失效）")
    if name not in FUNCS:
        continue
    eq(DECORATED.get(name), None,
       f"★ {name} 是內部 helper，不可被裝飾（被裝飾就不能再用一般方式呼叫，且會多開一個對外 endpoint）")

# backfill_ef_no_only 必須真的被 sync_formlabs_manual 呼叫得到
# （這是 backfill_ef_no 這條路徑唯一的入口，斷了就沒有別的方式可以跑）
eq("backfill_ef_no_only()" in ast.unparse(FUNCS["sync_formlabs_manual"]), True,
   "★ sync_formlabs_manual 要呼叫得到 backfill_ef_no_only()（前端沒有其他入口）")
eq("backfill_ef_no" in ast.unparse(FUNCS["sync_formlabs_manual"]), True,
   "sync_formlabs_manual 要看得懂 backfill_ef_no 這個參數")


# ── 5. 部署參數 ───────────────────────────────────────────────────────
# region 打錯不會報錯，只會部署到另一個地區去，前端照著 asia-east1 呼叫就找不到。
for name in EXPECTED_DEPLOYED:
    node = FUNCS.get(name)
    if node is None or not node.decorator_list:
        continue  # 沒有裝飾器的情況已在第 1 節報過，這裡不要再 IndexError 中斷整份測試
    kwargs = _dec_kwargs(node.decorator_list[0])
    eq(kwargs.get("region"), "asia-east1",
       f"★ {name} 要部署在 asia-east1（region 打錯只會靜默部署到別區）")

print(f"\n{_pass + _fail} 項：{_pass} PASS / {_fail} FAIL")
sys.exit(1 if _fail else 0)
