# CLAUDE.md — SWTC 3D列印設備管理系統

> 放在 repo 根目錄。Claude Code 每次 session 會自動讀，等於常駐版的交接文件。

## 專案概述
SWTC（3D列印設備代理商）內部管理系統。純 HTML/JS 前端（GitHub Pages）+ Firebase（Auth / Firestore / Cloud Functions Python 3.11）+ Formlabs Dashboard API（OAuth 2.0）+ GitHub Actions CI/CD。
- Repo：`jiliao2024092/swtc-3DP`（**D 大寫**）
- Firebase 專案：`swtc-3dp-poc`（region `asia-east1`），admin `jiliao@swtc.com`
- Pages 網址前綴：`https://jiliao2024092.github.io/swtc-3DP/`

## 工作守則（務必遵守）
1. **先講清楚假設**：不確定先問，不要自己猜。
2. **不過度工程化**：能簡單解決就不加戲；修 bug 就修 bug。
3. **不碰無關範圍**：不順手重構全世界。
4. **做完必須驗證**：改完跑對應語法/邏輯檢查（見下）再交付。
5. 思考過程用英文且減少顯示（省 token），結論與部署說明用中文。
6. 例行小改用較省的模型（Haiku/Sonnet），複雜除錯再切較強模型控成本。

## 架構重點（避免改錯檔）
portal.html 是 React 外殼（React18 + Babel CDN + Firebase compat SDK），但**不是每頁都寫在 portal.html 裡**：
- 工作看板 / 異常與資源 / 後台管理 → portal.html **內嵌** React 元件
- **3D列印機預約** → portal.html 用 `<iframe src="../3DP-BK.html">`（根目錄檔）
- **材料庫存管理** → portal.html 用 `<iframe src="../inventory.html">`（根目錄檔）
- **3D列印估價（Beta）** → portal.html 用 `<iframe src="../quote-studio.html">`（根目錄檔）。舊版 `quote.html` 已下線移除，之後「quote」一律指 `quote-studio.html`

→ 改預約/庫存/列印機狀態/估價的功能，要改根目錄的 `3DP-BK.html` / `inventory.html` / `quote-studio.html`，**不是** portal.html。

## 檔案地圖
- 根目錄：`inventory.html`（庫存）、`3DP-BK.html`（預約+列印機即時狀態）、`quote-studio.html`（3D列印估價 Beta）、`index.html`
- `portal/`：`portal.html`（外殼 + 看板/異常/後台元件 + 所有 modal/卡片 CSS）、`issues.js`、`workboard.js`、`firebase-config.js`、`firebase-service.js`（含 `PERMS_MAP`/`DEFAULT_ROLE_PRESETS` 角色權限定義）
- `functions/`：`main.py`（Formlabs 同步，entry：`sync_formlabs_scheduled` 每30分、`sync_formlabs_manual` admin）、`requirements.txt`
- `.github/workflows/`：`deploy-pages.yml`（push main 即全部署）、`deploy-functions.yml`（functions/ 有變動 → firebase deploy）
- `firestore.rules`：安全規則，改動後隨 push 自動 deploy（見下方部署段落）

## 部署
- 前端（根目錄檔或 portal 檔）：`git push` → GitHub Actions 自動部署 → 使用者 **Ctrl+Shift+R**（iframe cache 頑固，建議關分頁重開）
- **改 portal 本地 js（issues.js/workboard.js/firebase-*.js）後，務必升 portal.html 對應那支 `.js` 的 `?v=` cache 版本號**（每支各自獨立編號，只升有改動的那支；版本號會持續往上升，實際數值請直接看 `portal/portal.html` 內對應 `<script src="...?v=...">`，不要照抄這裡的舊範例）。只改 portal.html 自身（CSS/元件）不需升號
- Cloud Function：`git push`（functions/ 變動觸發），或 `firebase deploy --only functions --project swtc-3dp-poc`
- **有使用者看得到的改動時，要更新 `portal/portal.html` 的 `CHANGELOG` 陣列**（標題欄右下角 `?` 圓鈕的「版本更新說明」）。新的放最前面、維持 10 筆，`hash` 填該次的 commit short hash。這是純手動維護，沒人補就會停住（實際踩過：停在 2026-08-03 整整兩週，使用者回報「版本更新說明沒顯示」）。用**使用者看得懂的話**寫，不要貼 commit title
- ⚠ **改查詢形狀就要檢查複合索引**：`limitToLast(n)` 配 `orderBy(x,'asc')` 時，Firestore 是「反向掃描取前 N 筆再反轉」，真正需要的是 **`x DESCENDING`** 的複合索引，**不是**沿用既有的 ASC 那組。2026-08-25 實際事故：加了 `limitToLast` 沒補索引，單一地區的使用者每次查詢都 `failed-precondition`，工作看板／異常整頁空白且**毫無提示**；admin 不帶 `where` 不需要複合索引，所以他看得到——「一般人看不到、admin 看得到」這個落差就是缺索引的招牌症狀
- ⚠ **查詢失敗不可以只回空陣列**：空陣列與「這一區真的沒資料」在畫面上完全一樣。`firebase-service.js` 的 onSnapshot 錯誤處理會把 `error/code/hint` 一起回給呼叫端，工作看板與異常頁要顯示「資料載入失敗（不是沒有資料）」
- ⚠ **規則測試要測 `list` 不能只測 `get`**：Firestore 對兩者的判定不同，分區的坑幾乎都出在 list（`tools/rules-test` 已補 13 項真實查詢形狀的測試）
- `firestore.rules`：`git push` 即自動部署；**新增權限保護某個 collection/doc 時，務必檢查有沒有更泛用的萬用字元規則（如 `match /settings/{docId}`）會先蓋過具體路徑規則**——Firestore rules 是「最具體路徑優先」，不是疊加 OR，泛用規則若寫在前面且路徑更廣，會讓新權限完全不生效（實際踩過：`manage_quote_pricing`、`inventory_history` 主管刪除權限，都要在泛用規則之前另外寫具體路徑）

## 驗證指令（改完必跑；於 repo 根目錄執行）
```bash
# portal babel 區塊括號平衡
python3 -c "import re;h=open('portal/portal.html').read();ss=re.findall(r'<script type=\"text/babel\"[^>]*>(.*?)</script>',h,re.DOTALL);print('PASS' if all(s.count('{')==s.count('}') and s.count('(')==s.count(')') for s in ss if s.strip()) else 'FAIL')"

# issues.js 括號平衡
python3 -c "s=open('portal/issues.js').read();print(all(s.count(a)==s.count(b) for a,b in [('{','}'),('(',')'),('[',']')]))"

# 前端 module JS（3DP-BK.html / inventory.html）：抽出 module 區塊後 node 檢查
python3 -c "import re;h=open('3DP-BK.html').read();m=re.search(r'<script type=\"module\">(.*?)</script>',h,re.DOTALL);open('/tmp/x.js','w').write(m.group(1))"
node --input-type=module --check < /tmp/x.js

# Cloud Function
python3 -m py_compile functions/main.py

# 入庫的「已知材料」判斷：84 項（資料驅動，遍歷 CODE_TO_NAME / FAMILY_TO_NAME）
node tools/test_material_input.js

# 北中南分區邏輯：前端 115 項 + 後端 77 項（含前後端種子對照、追蹤機台名單三處一致性、Markforged 扣帳）
node tools/test_regions.js
python3 tools/test_regions_py.py

# 甘特圖「機台列 × 地區」：30 項（同機型每區各一份時不可互相顯示／不可讓舊資料消失）
node tools/test_gantt_rows.js

# firestore.rules 安全規則：98 項（跑本機 Firestore 模擬器，不連任何真實專案；含 list 查詢）
cd tools/rules-test && npm test
```
⚠ 規則測試需要 **JDK 21+**（firebase-tools 已不支援更舊版本）。本機的 JDK 在
`D:\web\swtc-3DP\jdk-21.0.12+8`（免安裝解壓版，未進版控）。若 `java -version` 不是 21+，
跑之前先設 `$env:JAVA_HOME`／`export JAVA_HOME` 指到該路徑並把 `$JAVA_HOME/bin` 放進 PATH。
模擬器設定在根目錄的 `firebase.emulator.json`——**刻意不併進 `firebase.json`**，
因為那個檔名列在 `deploy-functions.yml` 的 paths 過濾裡，動它會白白重新部署一次 Cloud Function。
JSX 若要更強保證：`npm i @babel/core @babel/preset-react`，再用 preset-react `transformSync` 逐一編譯各 babel 區塊（能編譯＝語法正確）。

## Firebase / 除錯
- 看 log：`firebase functions:log --project swtc-3dp-poc`。常搜 `[sync]`、`DEBUG目標print`、`DEBUG列印中無檔名`
- ⚠ **`firebase functions:log` 預設會被「部署稽核事件」洗版**（整頁 `google.cloud.audit.AuditLog`／`UpdateFunction`，看不到任何 `print()` 輸出）。要先濾掉：
  ```powershell
  firebase functions:log -n 300 --project swtc-3dp-poc |
    Select-String -NotMatch 'google.cloud.audit'
  ```
  漏掉這一步會誤以為「函式根本沒執行」，實際上是輸出被蓋掉了（2026-08-25 就是這樣繞了一圈）。
- ⚠ **`functions/requirements.txt` 的版本必須鎖死，且整份保持純 ASCII**（兩個獨立的坑）：
  - **不鎖版本**：2026-08-25 事故——`google-cloud-firestore` / `google-api-core` / `google-cloud-core` 三個套件都在 2026-08-24 發新版，隔天部署裝到之後，**每一輪同步都在第一次讀 Firestore 就掛**（`400 Invalid database id %28default%29`），而程式碼一行都沒改。已釘回 2026-08-06 那組。升級請一次升一個並確認 log 有成功訊息
  - **中文註解**：pip 是用**系統語系**讀 requirements（本機 cp950），非 ASCII 會直接 `UnicodeDecodeError` 讓安裝失敗
- ⚠ **這個事故的症狀很像「新功能沒生效」**：`printer_status/current` 是在失敗**之前**寫的，所以機台狀態看起來一切正常、6 台都在，只有消耗紀錄與庫存靜止。判斷「新版有沒有真的在跑」要看**該版本才會寫的欄位**（例如 `inventory/main.tracked_aliases_seeded`），不要看 `printer_status`
- ⚠ `%28default%29` 是 `(default)` 的 URL 編碼，但 firestore 客戶端的 `_database_string` **本來就長這樣**（實測 2.22.0～2.29.0 全部一致），**不是**判斷依據，別往那個方向查
- 主要 Firestore collection：`users`（`permissions` 陣列為主，`role` 是自動推導的舊系統相容值）、`bookings`（含跨天 `endDate`、用途 `category`）、`inventory/main`（全域帳務：去重用的 print guid、`family_latest_version`、產品層級設定）、`inventory/{north|central|south}`（各廠區樹脂實體庫存：stock／safety／cartridges／stock_shortfalls）、`inventory/markforged_{north|central|south}`（各廠區 Markforged 線材與耗材；舊的單一文件 `inventory/markforged` 保留為中區尚未建立時的唯讀來源）、`inventory_history/{guid}`（doc_id=guid 防重複；刪除消耗類紀錄會自動回補庫存）、`printer_status/current`、`workboard_orders`（`actUsage` 可從 inventory_history 自動帶入）、`issues_anomalies`、`issues_ipa`、`issues_equipment`、`settings/workspace`、`settings/quote_materials`、`settings/quote_studio_pricing`、`print_orders`、`print_history`
- GCP Secrets：`FORMLABS_CLIENT_ID`、`FORMLABS_CLIENT_SECRET`
- 機台（2026-08-18 由 `[region-scan]` / `[region-scan-mf]` log 實掃）：
  - Formlabs 6 台：`AluminumBowfin`(Form4·中)、`AdroitSauropod`(Form4L·中)、`JasperGosling`(Form4L·北)、`TealMoa`(Fuse1+·北)、`CreativeDragon`(Form3+·南)、`BoldSturgeon`(Form3L·南)
  - ⚠ **後三台的 `alias` 是 `None`，serial 就是機台名、沒有 `Form3L-` 這種前綴**——舊筆記寫的「機型靠 serial 前綴判斷」對它們無效，要改看 `machine_type_id`（`FORM-4-0`=Form4／`FRML-4-0`=Form4L／`FORM-3-2`=Form3+／`FRML-3-0`=Form3L／`FS30-1-0`=Fuse1+）
  - **納入消耗扣庫存的 5 台**（2026-08-25 起）：上述 6 台扣掉 `TealMoa`（Fuse 1+ 是 SLS 粉末，只顯示狀態不記消耗）。名單存在**三個地方，必須一起改**：`functions/main.py` 的 `TRACKED_ALIASES`、`inventory.html` 的 `TRACKED_PRINTERS`、`3DP-BK.html` 的 `MATERIAL_PRINTERS`
  - ⚠ 因為 alias 可能是 `None`，比對機台一律走 `alias or serial`（後端 `machine_key()`/`tracked_alias()`）。只看 `alias` 會讓南部兩台**完全不被追蹤且沒有任何錯誤訊息**：serial 進不了 `tracked_serials` → prints 根本不會被拉回來 → 消耗靜默消失
  - Markforged 納管 7 台（見 `EIGER_TRACKED_DEVICES`）；中國廠的 `Mark Two Dongguan`、`X7 Shanghai` **刻意排除**，白名單以外一律不寫入
  - **Markforged 已納入消耗扣帳**（2026-08-25 起，原為唯讀觀測模式）：靠 `ccs_*_remaining` 的差額判定用量，**只有「餘量下降」才扣**；refill／換料一律不動庫存（餘量上升是換料，那捲料早就從備料扣過了，當成加庫存會憑空生料）。消耗寫進 `inventory_history`（`source=markforged`、`unit=cc`）並扣 `inventory/markforged_{region}`
  - ⚠ 差額式追蹤的基準存在 `inventory/markforged_watch`，**基準更新與消耗寫入必須在同一個 batch**——分開寫會在「history 寫成功、基準寫失敗」時，讓下一輪用更舊的基準算出更大的一段差額，同一段消耗被記兩次、庫存也扣兩次
  - ⚠ Markforged 材料是**純名稱**（Onyx／Carbon Fiber），不可套 `canon_material()`／`family_code()` 那套 FL 家族代碼邏輯；扣庫存走 `apply_mf_deductions()`（比對純名稱、扣 `total_cc`），與樹脂的 `total_ml` 完全分開。耗材（`kind='consumable'`，以「個」計）不可被 cc 消耗扣到
  - ⚠ 機台顯示名稱有互為子字串的情況（`MarkTwo` ⊂ `MarkTwoGEN2` / `MarkTwoTainan`）。`machine_region()` 必須「完全相同優先、包含取最長」，只用包含比對會依 dict 鍵順序判錯區，且完全沒有錯誤訊息（`tools/test_regions*` 有守）

## 領域邏輯地雷
- **材料代碼家族正規化**（前後端須一致）：familyCode 取代碼前 6 碼，且須符合 `/^FL[A-Z0-9]{6}$/` 且含數字（避免 "Flexible" 被誤截）；有 FAMILY_REMAP / FAMILY_TO_NAME；所有計算函式按「家族」加總與去重；**總庫存 = 備料庫存**，機台樹脂罐純顯示（曾詢問過使用者是否要改成樹脂罐也計入總庫存，明確回答**不要**，維持現狀）
- **「是不是已知材料」不可用「家族碼是否含數字」判斷**：家族碼是完整 8 碼截斷成 6 碼的結果，截斷後往往就沒有數字了（`FLGPBK05` → `FLGPBK`）。21 個家族有 12 個會被誤判成未知材料（Clear/White/Grey/Black V5、High Temp、Elastic 50A、Fast/Precision Model、Flame Retardant、Ceramic、Polyurethane、Open Material），每次入庫都跳「不是內建材料名稱」，而警告還會建議使用者剛輸入的那個名稱。「含數字」是給**完整 8 碼**用的（避免 Flexible 被誤截成 FLEXIB），別套到家族碼。正解見 `isKnownMaterialInput()`，`tools/test_material_input.js` 有守
- **材料版本正規化在寫入 Firestore 前就發生**：`raw_material`（截斷前原始代碼）只在 `main.py` 處理當下短暫存在，`canon_material()`/`family_code()` 一執行完就只剩家族代碼，版本數字（如 FLTO2001 的 `01`）永久丟失。v2.2 新增的 `family_latest_version` 追蹤必須在截斷前（`raw_material` 還在時）掛勾，且只能影響「之後」同步的新資料，歷史紀錄無法回溯
- **消耗紀錄時間**：Formlabs 對 FINISHED 的 print 偶爾回傳 epoch(1970) 的 `print_finished_at`，會把紀錄打到 1970 而被前端 30 天視窗濾掉（看似漏抓）。已用 `parse_valid_ts`（年份<2000 視為無效）退回 `created_at`
- **消耗抓取**：用 `prints/?printer={serial}` 按 serial 過濾、無 date、無 sort、per-printer 分頁去重（勿改回 date+sort 全抓，會漏最新）
- **Firestore `.set()` 即使內容不變也計費一筆寫入**：`perform_sync` 對已在 `last_processed_prints` 的 guid 必須 `continue` 跳過，**勿改回「冪等重寫確保存在」**。曾因每輪重寫全部 ~777 筆 history × 每10分144次/天 ≈ 11萬寫入/天（免費額度僅2萬/天）爆量。要強制重建 history 改用 `sync_formlabs_manual` 的 backfill
- `.gitignore` 須含 `venv/ functions/venv/ **/venv/ __pycache__/`
- 「網頁沒更新」多半是 (a) 部署未觸發 或 (b) portal js 沒升 cache 版本號；若換無痕/換瀏覽器還是舊的 = 伺服器/CDN 端，非瀏覽器 cache

## Claude Code 在本專案：能做 / 需先設定
- **能**：直接改檔、跑上述驗證、`git add/commit/push`、`firebase deploy`、`firebase functions:log`（把「改→驗→commit→部署→看 log」整條龍收在一處）
- **需先設定**（一次性）：本機 `git clone` 此 repo、GitHub 推送憑證（PAT 或 SSH）、`firebase login`、（如需動 Secrets）`gcloud auth login`
- **權限**：保留逐次核准（不要一開始就用 `--dangerously-skip-permissions`），尤其 `push` / `deploy` 這種有副作用的指令
