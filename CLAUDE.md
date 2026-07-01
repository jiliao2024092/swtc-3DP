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
5. 思考過程用英文（省 token），結論與部署說明用中文。
6. 例行小改用較省的模型（Haiku/Sonnet），複雜除錯再切較強模型控成本。

## 架構重點（避免改錯檔）
portal.html 是 React 外殼（React18 + Babel CDN + Firebase compat SDK），但**不是每頁都寫在 portal.html 裡**：
- 工作看板 / 異常與資源 / 後台管理 → portal.html **內嵌** React 元件
- **3D列印機預約** → portal.html 用 `<iframe src="../3DP-BK.html">`（根目錄檔）
- **材料庫存管理** → portal.html 用 `<iframe src="../inventory.html">`（根目錄檔）

→ 改預約/庫存/列印機狀態的功能，要改根目錄的 `3DP-BK.html` / `inventory.html`，**不是** portal.html。

## 檔案地圖
- 根目錄：`inventory.html`（庫存）、`3DP-BK.html`（預約+列印機即時狀態）、`index.html`
- `portal/`：`portal.html`（外殼 + 看板/異常/後台元件 + 所有 modal/卡片 CSS）、`issues.js`、`workboard.js`、`firebase-config.js`、`firebase-service.js`
- `functions/`：`main.py`（Formlabs 同步 ~660 行，entry：`sync_formlabs_scheduled` 每10分、`sync_formlabs_manual` admin）、`requirements.txt`
- `.github/workflows/`：`deploy-pages.yml`（push main 即全部署）、`deploy-functions.yml`（functions/ 有變動 → firebase deploy）

## 部署
- 前端（根目錄檔或 portal 檔）：`git push` → GitHub Actions 自動部署 → 使用者 **Ctrl+Shift+R**（iframe cache 頑固，建議關分頁重開）
- **改 portal 本地 js（issues.js/workboard.js/firebase-*.js）後，務必升 portal.html 的 `?v=` cache 版本號**：目前 `20260629g`，下次升 `h`。只改 portal.html 自身（CSS/元件）不需升號
- Cloud Function：`git push`（functions/ 變動觸發），或 `firebase deploy --only functions --project swtc-3dp-poc`

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
```
JSX 若要更強保證：`npm i @babel/core @babel/preset-react`，再用 preset-react `transformSync` 逐一編譯各 babel 區塊（能編譯＝語法正確）。

## Firebase / 除錯
- 看 log：`firebase functions:log --project swtc-3dp-poc`。常搜 `[sync]`、`DEBUG目標print`、`DEBUG列印中無檔名`
- 主要 Firestore collection：`users`、`bookings`、`inventory/main`、`inventory_history/{guid}`（doc_id=guid 防重複）、`printer_status/current`、`workboard_orders`、`issues_anomalies`、`issues_ipa`、`issues_equipment`、`settings/workspace`
- GCP Secrets：`FORMLABS_CLIENT_ID`、`FORMLABS_CLIENT_SECRET`
- 機台：`AluminumBowfin`(serial `Form4-AluminumBowfin`→Form4)、`AdroitSauropod`(serial `Form4L-AdroitSauropod`→Form4L)

## 領域邏輯地雷
- **材料代碼家族正規化**（前後端須一致）：familyCode 取代碼前 6 碼，且須符合 `/^FL[A-Z0-9]{6}$/` 且含數字（避免 "Flexible" 被誤截）；有 FAMILY_REMAP / FAMILY_TO_NAME；所有計算函式按「家族」加總與去重；**總庫存 = 備料庫存**，機台樹脂罐純顯示
- **消耗紀錄時間**：Formlabs 對 FINISHED 的 print 偶爾回傳 epoch(1970) 的 `print_finished_at`，會把紀錄打到 1970 而被前端 30 天視窗濾掉（看似漏抓）。已用 `parse_valid_ts`（年份<2000 視為無效）退回 `created_at`
- **消耗抓取**：用 `prints/?printer={serial}` 按 serial 過濾、無 date、無 sort、per-printer 分頁去重（勿改回 date+sort 全抓，會漏最新）
- `.gitignore` 須含 `venv/ functions/venv/ **/venv/ __pycache__/`
- 「網頁沒更新」多半是 (a) 部署未觸發 或 (b) portal js 沒升 cache 版本號；若換無痕/換瀏覽器還是舊的 = 伺服器/CDN 端，非瀏覽器 cache

## Claude Code 在本專案：能做 / 需先設定
- **能**：直接改檔、跑上述驗證、`git add/commit/push`、`firebase deploy`、`firebase functions:log`（把「改→驗→commit→部署→看 log」整條龍收在一處）
- **需先設定**（一次性）：本機 `git clone` 此 repo、GitHub 推送憑證（PAT 或 SSH）、`firebase login`、（如需動 Secrets）`gcloud auth login`
- **權限**：保留逐次核准（不要一開始就用 `--dangerously-skip-permissions`），尤其 `push` / `deploy` 這種有副作用的指令
