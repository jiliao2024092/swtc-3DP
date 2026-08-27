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
- 根目錄：`inventory.html`（庫存）、`3DP-BK.html`（預約+列印機即時狀態）、`quote-studio.html`（3D列印估價 Beta）、`index.html`、`firebase-config.js`（**Firebase 設定的唯一來源**，五頁共用）、`appcheck.js`、`regions.js`
- `portal/`：`portal.html`（外殼 + 看板/異常/後台元件 + 所有 modal/卡片 CSS）、`issues.js`、`workboard.js`、`firebase-init.js`（compat SDK 初始化，**不含設定值**）、`firebase-service.js`（含 `PERMS_MAP`/`DEFAULT_ROLE_PRESETS` 角色權限定義）
- `functions/`：`main.py`（Formlabs 同步，entry：`sync_formlabs_scheduled` 每30分、`sync_formlabs_manual` admin）、`requirements.txt`
- `.github/workflows/`：`deploy-pages.yml`（push main 即全部署）、`deploy-functions.yml`（functions/ 有變動 → firebase deploy）
- `firestore.rules`：安全規則，改動後隨 push 自動 deploy（見下方部署段落）

## 部署
- 前端（根目錄檔或 portal 檔）：`git push` → GitHub Actions 自動部署 → 使用者 **Ctrl+Shift+R**（iframe cache 頑固，建議關分頁重開）
- **改 portal 本地 js（issues.js/workboard.js/firebase-*.js）後，務必升 portal.html 對應那支 `.js` 的 `?v=` cache 版本號**（每支各自獨立編號，只升有改動的那支；版本號會持續往上升，實際數值請直接看 `portal/portal.html` 內對應 `<script src="...?v=...">`，不要照抄這裡的舊範例）。只改 portal.html 自身（CSS/元件）不需升號
- Cloud Function：`git push`（functions/ 變動觸發），或 `firebase deploy --only functions --project swtc-3dp-poc`
- **有使用者看得到的改動時，要更新 `portal/portal.html` 的 `CHANGELOG` 陣列**（標題欄右下角 `?` 圓鈕的「版本更新說明」）。新的放最前面、維持 10 筆，`hash` 填該次的 commit short hash。這是純手動維護，沒人補就會停住（實際踩過：停在 2026-08-03 整整兩週，使用者回報「版本更新說明沒顯示」）。用**使用者看得懂的話**寫，不要貼 commit title
  - **`type` 分四類，標 `feat` 的門檻要守住**：`feat`（新功能）只給「**使用者以前完全做不到的事**」；既有功能的欄位／行為／位置變更一律 `tweak`（調整）；修 bug 用 `fix`；純文件用 `docs`。
    ⚠ 2026-08-27 踩過：連續 10 筆全掛 `feat`，其中 7 筆只是匯出格式的微調，真正的新功能被稀釋到看不出來。使用者主動反映後才改。
    一筆若混了多種改動，以「最大的那一項」為準（例：忘記密碼＋兩個小修正 → `feat`）。
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

# 北中南分區邏輯：前端 127 項 + 後端 77 項（含前後端種子對照、追蹤機台名單三處一致性、Markforged 扣帳、工程師清單分區）
node tools/test_regions.js
python3 tools/test_regions_py.py

# 甘特圖「機台列 × 地區」：30 項（同機型每區各一份時不可互相顯示／不可讓舊資料消失）
node tools/test_gantt_rows.js

# 扣庫存規則、Outcome 五分類、飛行中跳過、列印時間：69 項
python tools/test_deduct_outcome.py

# 列印記錄匯出（備註解析、收費規則、MF 併列、排序、列印結果、列印時間、中英文名）：110 項
node tools/test_print_log_export.js

# JSX 實際編譯（比括號平衡強：portal 的 babel 區塊 + workboard.js/issues.js）
# 需先 npm i @babel/core @babel/preset-react（可裝在 scratch 目錄，不必進版控）

# firestore.rules 安全規則：98 項（跑本機 Firestore 模擬器，不連任何真實專案；含 list 查詢）
cd tools/rules-test && npm test
```
⚠ 規則測試需要 **JDK 21+**（firebase-tools 已不支援更舊版本）。本機的 JDK 在
`D:\web\swtc-3DP\jdk-21.0.12+8`（免安裝解壓版，未進版控）。若 `java -version` 不是 21+，
跑之前先設 `$env:JAVA_HOME`／`export JAVA_HOME` 指到該路徑並把 `$JAVA_HOME/bin` 放進 PATH。
模擬器設定在根目錄的 `firebase.emulator.json`——**刻意不併進 `firebase.json`**，
因為那個檔名列在 `deploy-functions.yml` 的 paths 過濾裡，動它會白白重新部署一次 Cloud Function。
JSX 若要更強保證：`npm i @babel/core @babel/preset-react`，再用 preset-react `transformSync` 逐一編譯各 babel 區塊（能編譯＝語法正確）。

## Firebase 設定（唯一來源，2026-08-27 收斂）
- 設定值只在根目錄的 **`firebase-config.js`**，五個頁面共用：`portal/portal.html`（經 `portal/firebase-init.js`）、`3DP-BK.html`、`inventory.html`、`quote-studio.html`、`quote-markforged.html`。收斂前是 5 份硬編碼，改專案要同時改 5 個檔
- 取用一律走 **`window.requireFirebaseConfig()`**，拿不到會拋明確錯誤。少了這道防護，`initializeApp(undefined)` 會在很後面才冒出 `auth/invalid-api-key`，排查方向整個被帶偏
- ⚠ 用 **classic `<script>` 不是 ES module**：compat 頁（portal/quote-studio/quote-markforged）與 modular 頁（3DP-BK/inventory）都要能用。`<script type="module">` 預設 defer，一定在 classic script 之後執行，所以兩邊都讀得到。與 `appcheck.js` 同一個模式
- ⚠ 全域屬性刻意叫 `window.__FIREBASE_CONFIG__`：quote-studio / quote-markforged 頂層有 `const FIREBASE_CONFIG`，classic script 的頂層 const 會建立全域詞法繫結而**遮蔽同名的 window 屬性**，取名撞在一起遲早出事
- ⚠ **`appId` 收斂前有四種值**（`web:portal`／`web:quote`／`web:quotemf` 都是自己編的）。Auth 與 Firestore 確實不驗證 appId 所以一直沒出事，但 **App Check 綁的是「已註冊的 app」**——等 `appcheck.js` 的 `SITE_KEY` 填上去，那三頁會拿不到 token。現已統一成真實的 `1:1074210451221:web:30e84a3f501e90e612831c`
- 改完務必**五頁都開起來確認能登入**（改一個檔會同時影響全部）

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

## 列印記錄匯出 / Outcome（2026-08-27）
- **備註即 Formlabs 檔名**，慣例 `客戶簡稱-工作類別-第三段`。實掃 29 筆列印紀錄 **29/29 可解析**
  - ⚠ **分隔符要同時吃 `-` 與 `_`**（27 筆連字號、2 筆底線）。只 split `'-'` 會讓底線那幾筆的第2段變 `undefined` → 歸「未分類」，月度分析佔比少算且**畫面無徵兆**。`inventory.html` 與 `portal/workboard.js` **兩處都有這份解析**，改一處要記得改另一處
  - 第三段**純數字（8碼以上）= APP單號**、**文字 = 展示活動名稱**（海昌體驗營、翹曲試片…）
- **是否收費 = (工作類別==代工) OR (備註有APP單號)**。對照人工登記表 23 筆**全中**。⚠ 不可只看列印目的：「評估機器」在該表是 5 筆收費、1 筆不收費
- **列印目的**：代工→代工列印、評估→評估機器、工程測試→原廠材料工程測試。⚠ 目標表另有「正式立案前測試列印」，來源只有單一個「工程測試」，一對二**無法自動判別**，固定對到前者
- **Dashboard 的 Outcome 五分類**（實掃 1475 筆 prints 得出，`print_outcome()`）：
  | status | print_run_success | Outcome | 筆數 |
  |---|---|---|---|
  | FINISHED | SUCCESS | successful | 952 |
  | FINISHED | FAILURE | unsuccessful | 56 |
  | FINISHED | （無此欄） | printed | 220 |
  | ABORTED | （無此欄） | aborted | 175 |
  | ERROR | FAILURE | failed | 71 |
  - ⚠ `print_run_success` 是**巢狀 dict**，值在內層同名 key。直接拿整個 dict 比對 → 952 筆 successful 全誤判成 printed，**畫面看不出來**
  - ⚠ 官方文件範例的 `"UNKNOWN"` **實際一次都沒出現**；真實 enum 只有 `SUCCESS`/`FAILURE`，「Printed」是**欄位不存在**。照文件寫死會得到永遠不成立的分支
- **扣庫存規則（決策 B）**：只有 **Failed（ERROR）與 Aborted（ABORTED/ABORTING）不扣**，其餘全扣（含 **Unsuccessful —— 印完了、樹脂一樣消耗掉**）。刻意寫成**排除清單**（`NO_DEDUCT_OUTCOME_STATUSES`），讓 UNKNOWN／舊資料／未來新增的 enum 自動落在「扣」那側，不會靜默漏扣。仍以 `status` 判定而非 `outcome`：兩者對 Failed/Aborted 結論相同，但 `status` 的 enum 官方有完整文件
- ⚠ **「列印結果」欄不可用 `apiStatus`**：實測 29 筆消耗紀錄裡 **27 筆是 `PRINTING`**（那是抓取當下的機台狀態，不是該次列印的結果）。要用 `outcome` 欄位，2026-08-27 前的舊紀錄沒有此欄位（未 backfill）
- **飛行中不記消耗**（`IN_FLIGHT_STATUSES`，2026-08-27 起）：`PRINTING/PAUSED/PAUSING/PRECOAT/POSTCOAT` 這輪跳過，等變 FINISHED 再寫。原因：`doc_id=guid` 且處理過就**永不重寫**，飛行中寫入會讓 `apiStatus`/`outcome` 永遠停在當下那一刻（那 27 筆陳舊值就是這樣來的）
  - ⚠ **FC-118 風險**：Formlabs 偶爾對已印完的 print 永遠回傳 `PRINTING`，那種會被無限期跳過、消耗永不入帳。為此每輪把飛行中的檔名印進 log（`[sync] 飛行中`）——**同一個名字連續多天出現就是踩到了**
  - `PRINTING` **仍留在 `DONE_STATUSES`**：兩者分工不同（前者決定「這輪要不要現在寫」，後者決定「這個狀態算不算已結束」），不衝突
- ⚠ **「沒扣庫存」≠ 列印失敗**：四種未扣原因裡有三種（`outdated_version`/`backfill`/`newly_tracked_machine`）其實列印是成功的，只有 `failed_or_aborted` 才是失敗。匯出的成功/失敗判定見 `exportPrintResult()`
- **工程師／業務／機台清單分區**（2026-08-27）：後台三個工程師清單都有「地區」欄，**留空＝全區可見**（跨區支援的人一定要留空，否則那些區的人永遠指派不到他，畫面上毫無提示）。過濾在兩處實作：`portal/portal.html` 的 `inScope`（工作看板／異常與資源／機台共用）與 `3DP-BK.html` 的 `subscribeSettings`
  - ⚠ **只過濾「下拉能選誰」，絕不過濾名稱對照表**（`ENG_LABEL`/`ENG_FULLLABEL`）。舊資料可能指向別區的人，對照查不到會直接顯示英文 key，看起來像資料壞掉
  - admin 與可跨區檢視的主管不受限制
- **業務清單**：UI 在後台「**工作看板**」頁籤（原本在 3D列印機預約），工作看板業務欄／3D列印機預約／列印記錄匯出**三處共用同一份**。⚠ Firestore key 仍叫 `bk_sales` 是歷史因素，刻意不改名：改 key 要遷移既有資料，而三處都在讀它，漏一處的症狀是「業務下拉突然空白」
- **匯出的人名格式**：業務與責任工程師一律「**中文 (英文)**」（`zhEnLabel`），與 3DP-BK 的 `engDisplay`/`salesDisplay` 同一慣例。⚠ 對照查不到時**退回 key 而不是空字串**——空白會被當成「沒填」，但實際上工單有指定人，只是那人已不在清單裡
- **工程測試掛自家公司**（`ENG_TEST_COMPANY`＝實威國際股份有限公司）：
  - **客戶名稱**：只要是工程測試就帶入（人工登記表該類 7 筆全部如此）
  - **業務**：限「工程測試**且無單號**」才帶入（該表這類 5 筆全部如此，5/5）。⚠ 有單號的一律交給工單 join——工單上的業務才是真的指定人，預填會蓋掉它
  - 其餘類別靠單號 join 工單，因為備註第一段只有客戶**簡稱**不是 EIP 全名；join 時**不覆蓋**已填好的客戶名稱
- **業務欄**：`workboard_orders.sales`，清單與 3DP-BK 共用 `settings/workspace.bk_sales`（`window._settings_sales`）。列印記錄匯出以 **EF 單號** join 補「業務／客戶全名／責任工程師」三欄；**沒單號或對不到就留空給人工填**，不做客戶簡稱的模糊比對（撞名風險太高）
- **匯出合併規則**：塑料/纖維分兩列是 **Markforged 獨有**（doc_id 帶 slot），Formlabs 每筆 print 一律 **1:1 不合併**。踩過：對所有來源合併 → 29 筆被併成 13 列，還把不同材料的獨立列印加總（同檔名重印是常態，「實威-工程測試」就有 8 筆）
- ⚠ 匯出排序要用**時間數值**，不是 `toLocaleString` 的字串——字典序會把 `2026/8/7` 排在 `2026/8/27` 之前
- **匯出範圍＝「消耗記錄」分頁目前的篩選**（日期區間／材料／機台／工作類別）。表格與匯出共用 `applyHistoryFilters()`，⚠ **不可各寫一份**：畫面 12 筆、匯出 58 筆這種不一致兩邊都「看起來正常」，使用者無從察覺。日期比較含端點（`<` / `>`，不是 `<=` / `>=`）
- **列印時間**（`duration_hr`，2026-08-27 起）：來源 `elapsed_duration_ms`，缺漏時退回 `print_finished_at - print_started_at`，**無條件進位到 0.5 小時**（對齊人工填表習慣）
  - ⚠ **不可用 `estimated_duration_ms` 頂替**：那是排程用的預估值，填進「實際列印時間」是錯資料而且看不出來是估的。拿不到就回 `None`，匯出留空給人工填
  - ⚠ MF 合併列取**最大值不是加總**：塑料與纖維是同一次列印的兩條料，時間本來就是同一段，相加會變兩倍

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
