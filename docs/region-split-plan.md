# 北中南三區分區 — 實作計畫

> **狀態：已於 2026-08-21 全部實作完成並上線。** 這份保留作為「當初為什麼這樣決定」的
> 紀錄——決策 A~E 的取捨、Alpha/Beta 機制的缺陷與補救、階段順序不能顛倒的理由，
> 都還有參考價值。實際落地與後續待辦見 `HANDOFF-NEXT-SESSION.md`。
>
> ⚠ 兩處與現況不符（刻意保留原文，不改寫歷史）：
>   1. 決策 D 原本是「主管只能看不能編輯」，後來改成由 `view_all_regions` /
>      `edit_all_regions` 兩個**可勾選的權限**控制，不再綁角色。
>   2. §3b 的 Alpha/Beta 機制原本設想能控制資料範圍；Rules 收緊後它**只控制顯示層**。
>
> 對應待辦第 5~8 項。
> 使用者已選定範圍：**連資料也分區**（完整方案 A：單一 Firestore、每筆文件加 `region` 欄位，
> 靠 Rules + 查詢過濾把關；保留日後轉「Firestore 多具名資料庫」真正實體隔離的可能）。
>
> 讀這份之前請先讀 `CLAUDE.md` 的「架構重點」與「領域邏輯地雷」。

---

## 0. 三個會改變工作形狀的發現

查程式碼時發現的，跟原本的想像不一樣，**請先看這段再看後面**。

### 發現一：第 8 項「分區展開收合卡片」已經做好一半

`3DP-BK.html` 的 `renderStatusPanel()` 早就有分組摺疊的完整實作 —— 目前寫死分成
**「台中 / 其他」**兩組，有摺疊箭頭、機台數、`printerGroupCollapsed` 狀態。

```
3DP-BK.html:1950  const TAICHUNG_PRINTERS = ['AdroitSauropod','AluminumBowfin'];
3DP-BK.html:2051  function renderStatusPanel(printers)   // 已有分組 + 摺疊
```

→ 第 8 項不是從零做，而是把「寫死的台中/其他」換成「從設定讀的北/中/南」。
**工作量比預期小很多。**

### 發現二：系統裡的「機台」是**兩種不同的東西**，這是最大的設計岔路

| 用在哪 | 實際內容 | 來源 |
|---|---|---|
| 預約頁的設備下拉、甘特圖列 | **機型**：`Form4` / `Form4L` / `MarkTwo` / `UR` / `Other` | `settings/workspace.bk_machines` |
| 工作看板的機台欄 | **機型**：`Form4` / `Form4L` / `Fuse1+` / `Mark2` | `settings/workspace.machines` |
| 列印機即時狀態卡、庫存的樹脂罐 | **實體機台 alias**：`AluminumBowfin` / `AdroitSauropod` | Formlabs / Eiger API |

現在只有一個廠區，所以「機型」就足以指定一台機器。**三區之後會出問題**：三個廠區
可能各有一台 Form4，預約時只選「Form4」就不知道是哪一區的哪一台。

這件事**必須先決定**，因為它決定了資料模型（見 §2 決策 A）。

### 發現三：`printer_status/current` 與 `inventory/main` 是**單一文件**

```
functions/main.py:686   db.collection("printer_status").document("current").set({...}, merge=True)
functions/main.py:693   inv_ref = db.collection("inventory").document("main")
```

先前的構想是「每區一份文件」。但實際看下來，**`printer_status` 不需要拆**：它的
`printers[]` 是陣列，每個元素加一個 `region` 欄位就夠，前端照樣讀同一份文件再分組 ——
這比拆成三份文件少改非常多程式碼，也不會動到 Eiger 同步的 `merge=True` 那個地雷
（`CLAUDE.md` 有記：全量 set 會把 Markforged 狀態洗掉）。

`inventory/main` 就必須處理：`stock`（備料庫存）是各廠區自己的存貨，一定要分開。

---

## 1. 現況事實（已查證，含檔案位置）

### 資料

| Collection / Doc | 形狀 | 誰寫 | 分區難度 |
|---|---|---|---|
| `users/{uid}` | 每人一份 | 前端 | **易** — 加 `region` 欄位 |
| `bookings/{id}` | 每筆預約一份，有 `printer`（機型字串） | `3DP-BK.html:1466` | **易** — 加 `region` |
| `workboard_orders/{id}` | 每張工單一份，有 `machine` | `portal/workboard.js` | **易** — 加 `region` |
| `issues_*` | 每筆一份 | `portal/issues.js` | **易** — 加 `region` |
| `inventory_history/{guid}` | 每筆消耗一份，有 `printer`（alias） | Cloud Function | **中** — region 可由 printer 推導 |
| `printer_status/current` | **單一文件**，`printers[]` + `mf_printers[]` | Cloud Function | **中** — 陣列元素加 `region` |
| `inventory/main` | **單一文件**，`stock`(備料) / `cartridges`(依 alias) / `family_latest_version` … | Cloud Function + 前端 | **難** — `stock` 必須拆 |
| `settings/workspace` | **單一文件**，機台/工程師/業務/借用人清單 | AdminPanel | **難** — 每區一份清單 |

### 機台

- Formlabs 目前**只納管 2 台**：`functions/main.py` 的
  `TRACKED_ALIASES = ["AluminumBowfin", "AdroitSauropod"]`
- Markforged 只納管 1 台：`EIGER_TRACKED_DEVICES` 白名單，`Mark Two Taichung`
- 先前記錄的六台對照（北＝JasperGosling、TealMoa；中＝AluminumBowfin、AdroitSauropod；
  南＝CreativeDragon、BoldSturgeon）**目前程式碼裡不存在後四台**

### 權限

- `portal/firebase-service.js` 的 `PERMS_MAP` / `DEFAULT_ROLE_PRESETS`（admin/manager/operator/viewer）
- `roleTierOf()`：admin → manager（有 delete_*）→ operator（有 edit_board）→ viewer
- 「admin、主管除外」這條規則可直接用 `roleTierOf(u) === 'admin' || 'manager'` 判斷

---

## 2. 決策（已定案 2026-08-18）

| | 決定 |
|---|---|
| **A** | **A2** — 機台清單維持「機型」，但每區各有一份清單 |
| **B** | 四台都存在、同一個 Formlabs 組織帳號（1 組 secret 即可）。**Fuse 1+ 不記錄消耗庫存** |
| **C** | **C1** — `inventory/{region}` 三份文件 |
| **D** | 主管**只能看**其他區，不能編輯；admin 才能跨區編輯 |
| **E** | 沒有人跨兩區（主管是靠角色取得跨區檢視權，不是靠多個 region）。`users.region` 用**單一字串**，並要做防呆確保每人只有一區 |

### 機隊全貌（決策 B 的答案）

| 區 | 機台 alias | 機型 | 納入消耗扣庫存？ |
|---|---|---|---|
| 北 | JasperGosling | Form 4L | ✅ |
| 北 | TealMoa | Fuse 1+ | ❌ **不記錄消耗**（SLS 粉末，與樹脂體系不同） |
| 中 | AluminumBowfin | Form 4 | ✅（現況） |
| 中 | AdroitSauropod | Form 4L | ✅（現況） |
| 中 | Mark Two Taichung | Mark Two | 觀測模式（現況，見 `docs/markforged-integration-plan.md`） |
| 南 | CreativeDragon | Form 3+ | ✅ |
| 南 | BoldSturgeon | Form 3L | ✅ |

**A2 在這個機隊下沒有歧義**：每一區內每種機型都只有一台，所以預約時選「機型」
就等於指定了唯一一台實體機器。這正是 A2 成立的前提 ——
**日後同一區買第二台同機型時，A2 就會失效，屆時要升級成 A1。**（寫在這裡當提醒）

### 決策 E 的防呆要做在三個地方

1. **UI**：AdminPanel 的地區欄位是單選下拉，且**不可留空**
2. **Rules**：使用者不能改自己的 `region`（比照現有防提權模式）
3. **稽核**：後台使用者列表把「未設定地區」的人標紅，避免有人漏設而看不到任何資料

---

## 2b. 原始決策題目與分析（保留備查）

**這 5 題沒答案，我不會開始改程式碼。** 每題我都給了建議。

### 決策 A：預約與工單的「機台」要不要從機型改成實體機台？（最關鍵）

| 選項 | 做法 | 影響 |
|---|---|---|
| **A1（建議）** | 機台清單改成「實體機台」，每台掛一個 region 與機型。預約選的是「台中-Form4-AluminumBowfin」這種具體的機器 | 最乾淨，甘特圖一列一台真機。**但既有 bookings 的 `printer` 欄位值要遷移**（`Form4` → 某一台） |
| **A2** | 機台清單維持機型，但每區各有一份清單（`bk_machines` 變成每區一份） | 改動小。但同一區有兩台 Form4 時仍分不出來 |
| **A3** | 完全不動預約/工單的機台，只有「列印機即時狀態」分區 | 最小改動，**但這樣第 7 項「系統只顯示該角色所在地區的機台」在預約頁等於沒做到** |

> 我的建議是 **A1**，但它是這整件事裡唯一需要動到既有 `bookings` 資料內容的部分。
> 如果你希望這一輪先求穩，選 A2 也行，之後要升到 A1 仍可行。

### 決策 B：其他四台 Formlabs 機器現在存在嗎？

`TRACKED_ALIASES` 現在只有兩台。要做三區，需要知道：

1. JasperGosling / TealMoa / CreativeDragon / BoldSturgeon **這四台實際存在嗎？**
2. 它們跟現有兩台**在同一個 Formlabs 組織帳號底下嗎？**（決定是否只要一組 secret）
3. 這四台的 serial 前綴是什麼？（`Form4-XXX` / `Form4L-XXX` 決定機型判斷）

> 如果四台還沒到位，建議先做「北/中/南」三區的**架構**，但實際只有中區有機台，
> 之後機器進來直接在後台設定即可，不用再改程式。

### 決策 C：備料庫存怎麼分？

`inventory/main.stock` 是全公司一本帳。三區之後：

| 選項 | 做法 |
|---|---|
| **C1（建議）** | `inventory/{region}` 三份文件，各自有 `stock` / `safety` / `cartridges` |
| **C2** | 維持 `inventory/main`，把 `stock` 改成 `stock_by_region: { north:{}, central:{}, south:{} }` |

C1 較乾淨且未來好拆 DB；C2 改動較小但文件會越來越肥，且三區同時寫入會互相覆蓋
（Firestore 是文件級寫入衝突）。**建議 C1。**

> 不管哪個選項，**現有庫存數字全部歸「中」區**（先前已定案）。

### 決策 D：跨區可見度到什麼程度？

- 一般角色（operator / viewer）：**只看自己那區** —— 已定案
- **主管（manager）**：看全部三區，分區顯示可摺疊 —— 已定案
- 待確認：**主管能不能「編輯」其他區的資料**，還是只能看？

> 建議：主管**可看全部、只能編輯自己那區**，admin 才能跨區編輯。這樣權限邊界比較清楚，
> 也符合先前「防止主管越權」的設計慣例。

### 決策 E：有沒有人跨兩區？

先前記錄是「確認沒有一人跨兩區」。若成立，`users.region` 用單一字串即可（簡單得多）。
若有跨區的人，要改成陣列 `regions: []`，Rules 與所有查詢都會複雜一級。

> **請再確認一次**，這決定資料型別，改起來很痛。

---

## 3. 資料模型（依上面的建議選項）

```
region 代碼統一用：'north' | 'central' | 'south'
顯示名稱：北部 / 中部 / 南部（前端對照表，不要把中文存進 Firestore）
```

| 位置 | 新增欄位 |
|---|---|
| `users/{uid}` | `region: 'central'` |
| `bookings/{id}` | `region: 'central'` |
| `workboard_orders/{id}` | `region: 'central'` |
| `issues_*/{id}` | `region: 'central'` |
| `inventory_history/{guid}` | `region`（Cloud Function 依 printer alias 推導） |
| `printer_status/current` | `printers[].region`、`mf_printers[].region` |
| `inventory/{region}` | 由 `inventory/main` 拆出（決策 C1） |
| `settings/workspace` | `machines[].region`、`bk_machines[].region`；工程師/業務清單各自加 `region` |

**機台→區的對照放哪**：放 `settings/workspace`，由 admin 在後台設定（第 6 項的要求）。
Cloud Function 同步時讀這份設定決定要把資料寫到哪一區 —— **不要在 `main.py` 裡再寫死一份對照表**，
否則會出現兩個真相來源。

---

## 3b. Alpha / Beta 分段上線機制

使用者要求「先做 Alpha 版，確認沒問題後再上 Beta」。做法是加一個**總開關**，
存在 `settings/workspace.region_mode`，由 admin 在後台切換：

| 模式 | 誰看得到分區 | 用途 |
|---|---|---|
| `off`（預設） | 沒有人 | 分區資料照寫，但畫面完全維持現況。**階段 1~3 上線後就停在這裡** |
| `alpha` | **只有 admin** | admin 自己把三區資料、機台歸屬、過濾邏輯全部走一遍，其他同事完全無感 |
| `beta` | admin + 主管 | 各區主管實際試用、回報。一般同事仍看現況 |
| `on` | 所有人 | 正式上線 |

**關鍵性質：切換模式不需要重新部署**，改 Firestore 一個欄位即可，出事就切回 `off`，
**秒級回滾**。這比用 git revert 快得多，也是選這個做法而不是用分支的原因。

實作上就是一個判斷函式，前端所有分區相關的顯示都問它：

```
regionActiveFor(user) → true 表示這個人要套用分區顯示
  off   → false
  alpha → roleTier === 'admin'
  beta  → roleTier === 'admin' | 'manager'
  on    → true
```

### ⚠ 這個機制的一個缺陷（實際測出來才發現，2026-08-18）

`alpha` 只納入 admin、`beta` 多納入主管 —— 但依決策 D，**這兩種角色本來就可跨區檢視**，
所以他們的畫面在 alpha/beta 完全沒有變化。**唯一會被過濾影響的一般使用者，正好是
alpha/beta 排除掉的那群人。** 換句話說，alpha/beta 對「驗證過濾是否正確」毫無幫助。

使用者實測踩到：用北部的工程師帳號在 alpha 模式登入，看到的仍是未套用分區的畫面。

補救：機台狀態卡加了一個**「檢視地區」切換**（只有可跨區的人看得到），
admin 不必把開關切到 `on` 影響所有同事，就能看到某一區的人實際會看到的畫面。
純前端顯示過濾，不改權限、不寫入設定，重新整理即回到「全部」。

> ⚠️ **Rules 收緊（階段 5）不受這個開關控制** —— Rules 是伺服器端的硬邊界，一旦收緊就
> 對所有人生效，沒有 alpha/beta 之分。所以**階段 5 必須等 `on` 模式穩定跑一段時間後才做**，
> 順序不能顛倒。

---

## 4. 實作順序（5 階段，每階段可獨立上線與回滾）

刻意設計成「先加東西、最後才打開開關」，任何一階段出問題都不會讓系統停擺。

### 階段 1：只加欄位，不改行為（零風險）

- `region` 常數與對照表（前後端各一份，值必須一致）
- `settings/workspace` 的機台項目支援 `region` 欄位（沒設 → 視為 `central`）
- `users` 支援 `region` 欄位（沒設 → 視為 `central`）
- **所有讀取邏輯完全不看 region**，畫面與現在一模一樣

→ 可獨立 push 驗收。這一階段做完，系統行為零變化。

### 階段 2：admin 後台可以設定（第 5、6 項）

- AdminPanel 使用者編輯視窗加「地區」下拉
- AdminPanel 機台清單每台加「地區」下拉
- `firestore.rules`：使用者不能改自己的 `region`（比照現有的防提權模式）

→ 可獨立 push。此時資料有了，但還沒有人依它過濾。

### 階段 3：新資料開始帶 region（仍不過濾）

- 前端新增預約/工單/異常時寫入 `region`（＝當前使用者的區）
- Cloud Function 依機台設定寫 `printer_status[].region` 與 `inventory_history.region`
- **一次性遷移腳本**：既有資料全部補 `region: 'central'`

→ 這一階段有資料遷移，**動手前必須先 export 備份**（見 §5）。

### 階段 4：打開顯示過濾（第 7、8 項）— 走 Alpha → Beta → 正式

- `3DP-BK.html` 的 `renderStatusPanel()`：`TAICHUNG_PRINTERS` 寫死的分組改成讀設定的北/中/南
- 一般角色只看自己那區；admin/主管看全部、分區摺疊
- 預約設備下拉、甘特圖列、工作看板機台下拉、庫存樹脂罐同步過濾
- **全部包在 `regionActiveFor(user)` 之下**（見 §3b）

上線後的節奏：

1. push 完先維持 `region_mode: 'off'` → 確認所有人畫面沒變
2. 切 `alpha` → 只有 admin 看得到，你自己把三區走一遍
3. 切 `beta` → 各區主管試用回報
4. 切 `on` → 全面生效

→ 任何一步有問題，改回 `off` 即可，**不需要重新部署**。

### 階段 5：Rules 層收緊（真正的隔離）— 要等 `on` 穩定後才做

- `firestore.rules` 對各 collection 加 region 比對
- 前端過濾只是 UI，Rules 才是真的擋

> ⚠️ **這一階段有一個地雷**：Firestore 對 collection 查詢是「查詢必須保證只回傳讀得到的文件」，
> 所以 Rules 加了 region 條件之後，**前端的查詢也必須帶上對應的 `where('region','==',...)`**，
> 否則整個查詢會直接失敗（不是過濾掉，是全部讀不到）。階段 4 與階段 5 的查詢改動要對齊。

---

## 5. 備份與回滾

### 動手前（階段 3 之前）必做

```bash
gcloud firestore export gs://swtc-3dp-poc-backup/pre-region-split-$(date +%Y%m%d) \
  --project=swtc-3dp-poc
```

> bucket 若不存在要先建。這是**唯一**能完整還原的手段 —— Firestore 沒有「復原到某時間點」
> 的免費功能（PITR 要付費且需事先啟用）。

### 回滾方式

| 階段 | 回滾 |
|---|---|
| 1、2 | `git revert` 即可，資料只是多了沒人看的欄位 |
| 3 | `git revert` + 欄位留著不影響（因為還沒人依它過濾） |
| 4 | `git revert` 前端，Rules 沒動，資料完好 |
| 5 | **Rules 要一起 revert**，否則前端 revert 後查詢會被擋 |

---

## 6. 風險清單

| 風險 | 說明 | 對策 |
|---|---|---|
| **查詢與 Rules 不同步** | 階段 5 的地雷，會讓整頁讀不到資料 | 階段 4、5 的查詢改動寫在一起，分開 push 但一起驗 |
| **`settings/workspace` 是單一文件** | 三區的設定塞同一份，admin 同時編輯會互相覆蓋 | 短期可接受（只有 admin 編輯）；日後量大再拆 |
| **既有 `bookings.printer` 值遷移**（決策 A1 才有） | 機型 → 實體機台的對應是**多對一，無法自動判斷** | 全部歸中區的對應機台，並在遷移腳本印出清單供人工核對 |
| **Cloud Function 寫入量** | 加欄位不影響筆數，但 `inventory/{region}` 三份文件 = 每輪最多 3 次寫入（原本 1 次） | 只寫「本輪真的有變動」的區，沿用 `perform_sync` 既有的 `continue` 跳過模式（`CLAUDE.md` 有記過寫入爆量前例） |
| **iframe cache** | 預約/庫存是 iframe 載入，改版後使用者看到舊畫面 | 每階段上線都要請使用者關分頁重開，不是只按 Ctrl+Shift+R |
| **`family_latest_version` 分區後失準** | 若三區用不同批次的材料，版本追蹤要不要也分區？ | **待確認**。建議先維持全公司一份（材料版本是產品層級的事實，不是廠區的） |

---

## 7. 工作量估計

| 階段 | 估計 | 備註 |
|---|---|---|
| 1 | 小 | 只加欄位與對照表 |
| 2 | 中 | 兩個後台 UI + rules |
| 3 | **中～大** | 含一次性遷移腳本與備份 |
| 4 | 中 | 分組摺疊已有現成實作可改（發現一） |
| 5 | 中 | Rules + 查詢對齊，要仔細測 |

決策 A 選 A1 的話，階段 3 會明顯變重（多了 `bookings.printer` 的資料遷移與人工核對）。

---

## 8. 仍待確認（不擋階段 1、2，但階段 3 之前要有答案）

1. **機台→區的對照是否就是 §2 那張表？** 先前記錄的北/中/南歸屬與這次給的機台清單一致，
   但請再確認一次 —— 這張表會成為 `settings/workspace` 的初始值。
2. **Fuse 1+（TealMoa）的機台狀態卡要顯示嗎？** 已確認「不記錄消耗庫存」，但「顯示機台
   即時狀態」是另一件事。我的假設是**要顯示**（北區同事需要知道機器在不在跑），只是不扣料。
3. **Form 3+ / Form 3L 的 serial 前綴**：Cloud Function 是靠 serial 判機型
   （現況 `Form4-AluminumBowfin` → Form4）。這四台的 serial 實際長相要從 API 撈一次才知道，
   我會在階段 3 加一行 log 印出來，不用你先查。

## 9. 下一步

決策 A~E 已定案，**從階段 1 開始**，每階段各自 push 讓你驗收。
分區顯示會走 §3b 的 `off → alpha → beta → on`，切換不需重新部署。
