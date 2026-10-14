# -*- coding: utf-8 -*-
"""tools/test_create_user.py — 後台「新增使用者」Cloud Function（create_user）的權限與欄位

為什麼需要（2026-10-13）：
    新增使用者原本在前端用 createUserWithEmailAndPassword，Firebase Auth 的「開放註冊」
    因此必須開著，任何拿到公開 API key 的人都能自行註冊。改由 create_user（Admin SDK）
    建帳號之後才能關掉開放註冊。Admin SDK 不經過 firestore.rules，所以規則裡
    「主管不可建出管理員」那條邊界，這支函式要自己守一次 —— 這裡就是在測那一次。

測的東西：
  1. 誰能呼叫（user_caps）：admin（permissions 或舊 role）、manage_users 主管；停用的不行
  2. 寫進 users 文件的內容（new_user_doc）：只收 portal 的形狀、role 由伺服器推導、
     主管不可建管理員、地區必填且在白名單內
  3. role_from_permissions 與前端 roleFromPermissions() 結果一致（實際用 node 執行前端那段）
  4. 前端不可再出現 createUserWithEmailAndPassword（開放註冊關掉後那條路會直接失敗）

執行：python tools/test_create_user.py     （於 repo 根目錄）
"""
import ast
import io
import json
import os
import re
import subprocess
import sys

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


def raises(fn, exc, why):
    global _pass, _fail
    try:
        fn()
    except exc:
        _pass += 1
        return
    except Exception as e:  # 丟錯種類也要對：PermissionError 與 ValueError 對應不同的錯誤碼
        _fail += 1
        print(f"FAIL  {why}\n      丟出 {type(e).__name__}: {e}（預期 {exc.__name__}）")
        return
    _fail += 1
    print(f"FAIL  {why}\n      沒有丟出 {exc.__name__}")


# ── 從 main.py 抽出要測的頂層定義，實際執行（main.py 本身 import 了 firebase，測試環境不一定有）──
WANT = {"REGION_CODES", "role_from_permissions", "user_caps", "new_user_doc"}
picked = []
for node in TREE.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        picked.append(node)
    elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in WANT for t in node.targets):
        picked.append(node)
NS = {}
exec(compile(ast.Module(body=picked, type_ignores=[]), "main.py", "exec"), NS)
for name in WANT:
    eq(name in NS, True, f"★ main.py 要找得到 {name}（找不到通常是改名了，這份測試會跟著失效）")

role_from_permissions = NS["role_from_permissions"]
user_caps = NS["user_caps"]
new_user_doc = NS["new_user_doc"]

# ── 1. 誰能呼叫 ──────────────────────────────────────────────────────
print("── 誰能呼叫（user_caps）──")
eq(user_caps({"permissions": ["admin"]}), (True, True), "permissions 含 admin → admin")
eq(user_caps({"role": "admin"}), (True, True),
   "★ 只有舊 role:'admin'（3DP-BK 建的管理員）也是 admin，與 rules 的 hasPerm() 一致")
eq(user_caps({"permissions": ["view_board", "manage_users"]}), (False, True),
   "manage_users 主管可以新增，但不是 admin")
eq(user_caps({"permissions": ["view_board", "edit_board"], "role": "editor"}), (False, False),
   "工程師不可新增使用者")
eq(user_caps({"role": "editor"}), (False, False), "舊 role:'editor' 不可新增使用者")
eq(user_caps({"permissions": ["admin"], "active": False}), (False, False),
   "★ 停用的 admin 不可新增（停用的人在 token 過期前仍算登入）")
eq(user_caps({"permissions": ["admin"], "active": True}), (True, True), "active:true 照常")
eq(user_caps(None), (False, False), "★ 沒有 users 文件的帳號不可新增")
eq(user_caps({"permissions": "admin"}), (False, False),
   "permissions 不是陣列（壞資料）不可被當成含 admin（字串的 in 是子字串比對）")

# ── 2. 寫進 users 文件的內容 ────────────────────────────────────────
print("── portal 的形狀 ──")
portal = {"email": " new@swtc.com ", "password": "secret1", "displayName": " 新同事 ",
          "permissions": ["view_board", "edit_board"], "active": True, "region": "south"}
d = new_user_doc(portal, caller_is_admin=False)
eq(sorted(d.keys()), sorted(["email", "displayName", "permissions", "role", "active", "region"]),
   "★ portal 形狀的欄位（password 絕不可寫進文件）")
eq(d["email"], "new@swtc.com", "email 去頭尾空白")
eq(d["displayName"], "新同事", "顯示名稱去頭尾空白")
eq(d["role"], "editor", "role 由 permissions 推導")
eq(d["region"], "south", "★ 寫入 UserModal 選的地區（原本前端 createUser 把它丟掉，一律變中部）")
eq(new_user_doc({**portal, "role": "admin"}, False)["role"], "editor",
   "★ portal 形狀不採用前端傳來的 role（否則主管送 role:'admin' 就繞過了）")
eq(new_user_doc({**portal, "active": False}, False)["active"], False, "可以建立停用狀態的帳號")
eq("active" in new_user_doc({k: v for k, v in portal.items() if k != "active"}, False), True,
   "沒送 active → 預設啟用")
eq(new_user_doc({**portal, "createdBy": "偽造", "uid": "x"}, False).get("createdBy"), None,
   "★ 白名單以外的欄位不可寫進去（createdBy 由伺服器填呼叫者）")
raises(lambda: new_user_doc({**portal, "permissions": ["view_board", "admin"]}, False),
       PermissionError, "★ 主管不可建立 permissions 含 admin 的帳號")
eq(new_user_doc({**portal, "permissions": ["admin"]}, True)["role"], "admin",
   "admin 可以建立管理員，role 跟著是 admin")
raises(lambda: new_user_doc({**portal, "permissions": "view_board"}, True),
       ValueError, "permissions 不是陣列 → 格式錯誤")
raises(lambda: new_user_doc({**portal, "permissions": ["view_board", 3]}, True),
       ValueError, "permissions 裡有非字串 → 格式錯誤")
raises(lambda: new_user_doc({**portal, "permissions": ["view_board", ""]}, True),
       ValueError, "permissions 裡有空字串 → 格式錯誤")
raises(lambda: new_user_doc({**portal, "active": "yes"}, True),
       ValueError, "active 不是布林 → 格式錯誤")
raises(lambda: new_user_doc({**portal, "region": "偷改的區"}, True),
       ValueError, "★ 地區只收 north／central／south")
raises(lambda: new_user_doc({**portal, "region": None}, True),
       ValueError, "地區送 null → 格式錯誤（不可靜默變中部）")
raises(lambda: new_user_doc({k: v for k, v in portal.items() if k != "region"}, True),
       ValueError, "★ 地區必填：沒送就拒絕（沒地區的帳號會被當成中部，畫面上沒有提示）")
raises(lambda: new_user_doc({**portal, "email": "  "}, True), ValueError, "email 空白 → 錯誤")
raises(lambda: new_user_doc({k: v for k, v in portal.items() if k != "email"}, True),
       ValueError, "沒送 email → 錯誤")

print("── 只有 role 的舊寫法（原 3DP-BK 使用者管理，2026-10-14 移除）──")
legacy = {"email": "bk@swtc.com", "password": "secret1", "displayName": "bk", "role": "admin", "region": "central"}
raises(lambda: new_user_doc(legacy, False),
       ValueError, "★ 主管只送 role:'admin'、沒有 permissions → 拒絕（不可靠舊 role 欄位建出管理員）")
raises(lambda: new_user_doc(legacy, True),
       ValueError, "admin 也一樣：沒有 permissions 一律拒絕，只收 portal 的形狀")

# ── 3. 與前端 roleFromPermissions() 一致 ─────────────────────────────
print("── role 推導：前後端一致 ──")
CASES = [[], ["view_board"], ["edit_board"], ["edit_issues"], ["view_issues", "delete_issues"],
         ["admin"], ["view_board", "admin"], ["manage_users", "delete_board"], ["edit_board", "admin"]]
JS = io.open(os.path.join(ROOT, "portal", "firebase-service.js"), encoding="utf-8").read()
m = re.search(r"^  function roleFromPermissions\(permissions\) \{.*?^  \}", JS, re.M | re.S)
eq(bool(m), True, "★ 抓得到 firebase-service.js 的 roleFromPermissions（改名或改縮排這裡會失效）")
if m:
    script = m.group(0) + "\nconsole.log(JSON.stringify(" + json.dumps(CASES) + ".map(roleFromPermissions)));"
    try:
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        js_roles = json.loads(out.stdout)
        eq(js_roles, [role_from_permissions(c) for c in CASES],
           "★ role_from_permissions 與前端 roleFromPermissions 每一組都相同")
    except (OSError, ValueError) as e:
        eq(str(e), "", "要能用 node 執行前端那段（本機需要 node）")

# ── 4. handler 本身：一定要經過上面兩道、文件寫失敗要回滾 ──────────────
print("── create_user 的流程 ──")
handler = next((n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "create_user"), None)
eq(handler is not None, True, "main.py 要找得到 create_user")
if handler is not None:
    body = ast.unparse(handler)
    eq("user_caps(" in body, True, "★ create_user 要用 user_caps 判斷呼叫者")
    eq("new_user_doc(" in body, True, "★ create_user 寫入的內容要經過 new_user_doc")
    eq("auth.delete_user(" in body, True,
       "★ users 文件寫失敗要回滾 Auth 帳號（否則 email 被佔住、後台又看不到這個人）")

# ── 5. 前端不可再用 createUserWithEmailAndPassword ───────────────────
print("── 前端不可改回 createUserWithEmailAndPassword ──")
FRONT = ["3DP-BK.html", "inventory.html", "quote-studio.html", "quote-markforged.html", "index.html",
         os.path.join("portal", "portal.html")]
FRONT += [os.path.join("portal", f) for f in os.listdir(os.path.join(ROOT, "portal")) if f.endswith(".js")]
for f in FRONT:
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        continue
    # 比對「呼叫」而不是名稱：檔案裡警告「不可改回」的註解本身也會提到這個名稱
    eq(bool(re.search(r"createUserWithEmailAndPassword\s*\(", io.open(p, encoding="utf-8").read())), False,
       f"★ {f} 不可呼叫 createUserWithEmailAndPassword（Auth 開放註冊已關，要走 create_user）")

print(f"\n{_pass + _fail} 項：{_pass} PASS / {_fail} FAIL")
sys.exit(1 if _fail else 0)
