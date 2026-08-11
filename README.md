# 假日加班登記系統

原本的 `假日加班即時登記_demo.html` 是純前端 demo。這裡是可以真的上線用的版本：
有登入、資料存在資料庫、伺服器蓋時間戳、主管線上簽核，以及一個後台可以設定
**「每個人的加班登記要發給誰」**。

**同一份程式碼可以兩種方式跑：**

| | 資料庫 | 適合 |
|---|---|---|
| **本機** `python3 app.py` | SQLite（`data/overtime.db`） | 自己測試、同網段示範 |
| **雲端** Vercel | Postgres（Neon） | 正式使用，不依賴你的電腦 |

切換方式：有設定環境變數 `DATABASE_URL` 就走 Postgres，沒有就走 SQLite。程式不用改。

---

# A. 本機執行

在 Finder 裡雙擊 **`啟動伺服器.command`**，或在終端機執行：

```bash
cd ~/Downloads/overtime-server && /usr/bin/python3 app.py
```

啟動後會印出網址：你自己用 `http://localhost:8080`，同事用 `http://<你的區網IP>:8080`。
不需要安裝任何套件（用 macOS 內建的 Python 3）。

### 常用參數

```bash
/usr/bin/python3 app.py --port 9000        # 換連接埠
/usr/bin/python3 app.py --https            # 自簽憑證 HTTPS（瀏覽器需手動信任）
/usr/bin/python3 app.py --reset-admin      # 忘記管理員密碼，重設並印出新密碼
/usr/bin/python3 app.py --no-demo          # 第一次啟動不要建示範帳號
/usr/bin/python3 app.py --check-db "URL"   # 檢查雲端 Postgres 連線（見 B 步驟 3）
```

### 帳號

第一次啟動時建立，密碼印在終端機，也存在 `data/FIRST_RUN.txt`（看完請刪掉）。

| 帳號 | 身分 | 密碼 |
|---|---|---|
| `admin` | 管理員 | 隨機產生，第一次登入強制改密碼 |
| `ming`／`mei` | 員工（王小明、李小美） | `demo1234` |
| `chen` | 員工（陳大明，是上面兩人的簽核主管） | `demo1234` |

**正式使用前請刪掉三個示範帳號。**

---

# B. 部署到雲端（Vercel + Neon）

全程用瀏覽器操作，不需要 CLI（Vercel CLI 需要 Node.js，這台電腦沒有）。
預計 20～30 分鐘。

## 步驟 1：申請 GitHub 帳號並推上程式碼

程式碼已經是一個 git repo 了（`data/` 已排除，資料庫和密碼不會外流）。

1. 到 <https://github.com/signup> 註冊（免費）
2. 登入後點右上 **+ → New repository**
   - Repository name：`overtime-server`
   - **選 Private**（重要：這是公司內部系統）
   - 不要勾任何 initialize 選項
   - 按 Create repository
3. 回到終端機，把下面的 `你的帳號` 換成實際的 GitHub 帳號後執行：

```bash
cd ~/Downloads/overtime-server && git remote add origin https://github.com/你的帳號/overtime-server.git && git push -u origin main
```

推送時會要求登入。**密碼欄不能用 GitHub 密碼**，要用 Personal Access Token：
<https://github.com/settings/tokens> → Generate new token (classic) → 勾選 `repo` → 產生後複製貼上。

## 步驟 2：建立 Neon 資料庫（免費，不用信用卡）

1. 到 <https://neon.tech> → Sign up（可以用 GitHub 帳號登入）
2. Create project：Name 隨意、Region 選 **Singapore** 或 **Tokyo**（離台灣最近）
3. 建好後在 Dashboard 找 **Connection string**
4. **一定要選「Pooled connection」**（serverless 一定要用連線池，否則連線數會爆掉）
5. 複製那串網址，長得像：
   ```
   postgresql://neondb_owner:xxxxx@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```

## 步驟 3：先在本機驗證這組連線字串（強烈建議）

部署完才發現連不上很難查，先在本機測：

```bash
/usr/bin/python3 -m pip install --user psycopg2-binary
cd ~/Downloads/overtime-server && /usr/bin/python3 app.py --check-db "把剛才複製的連線字串貼在這裡"
```

它會連線、建立所有資料表、實際寫入一筆測試資料、跑聚合與關聯查詢，最後把測試資料清掉。
看到「全部通過 ✅」就可以往下走。

## 步驟 4：部署到 Vercel

1. 到 <https://vercel.com/signup> → 用 GitHub 帳號登入
2. **Add New… → Project** → 找到 `overtime-server` → **Import**
3. Framework Preset 保持 **Other**（Vercel 會自己認出 Python）
4. 展開 **Environment Variables**，加入：

| Name | Value |
|---|---|
| `DATABASE_URL` | 步驟 2 的 Pooled connection string |
| `ADMIN_INITIAL_PASSWORD` | 你自己想一組管理員初始密碼（至少 8 字、混用兩種以上字元） |
| `COMPANY_NAME` | 公司名稱（選填） |

5. 按 **Deploy**，等 1～2 分鐘
6. 打開它給的網址 → 用 `admin` ＋ 你設定的 `ADMIN_INITIAL_PASSWORD` 登入 → 系統會要求改密碼
7. 進後台「員工與簽核路由」→ 新增員工、設定每個人的簽核主管與副本收件人

網址會像 `https://overtime-server-xxxx.vercel.app`，全球都連得到、有 HTTPS、不依賴你的電腦。
之後每次 `git push`，Vercel 會自動重新部署。

## 步驟 5：讓通知信真的寄出

雲端 serverless 對外連 SMTP 常被擋，所以建議用 **Resend** 的 HTTP API：

1. <https://resend.com> 註冊（免費方案每月 3000 封）
2. 拿 API Key
3. 回到 Vercel 專案 → Settings → Environment Variables → 加 `RESEND_API_KEY`，然後 Redeploy
4. 系統後台 →「系統設定」→ 通知信寄送方式選 **Resend** → 儲存 → 按「寄一封測試信」

> ⚠️ Resend 沒有驗證自己的網域之前，**只能寄到你註冊 Resend 用的那個信箱**。
> 要寄給主管和 HR，必須在 Resend 加入公司網域並完成 DNS 驗證（需要 IT 幫忙加 DNS 記錄）。
> 在那之前，系統可以先用「模擬」模式：信件內容都存在後台寄件匣，流程照跑。

公司有自己的郵件主機、而且 Vercel 連得出去的話，也可以改用 SMTP 模式（後台填主機與帳密）。

## Vercel 方案的注意事項

- **Hobby（免費）方案禁止商業用途**。公司內部工具嚴格來說要用 Pro（約 $20/月）。
  自己測試沒問題，要正式給同事用請確認方案。
- **冷啟動**：一段時間沒人用之後，第一個請求會慢 1～3 秒，之後就正常。
- **背景寄信改成同步**：serverless 沒有背景執行緒，信會在送出登記的那個請求裡直接寄出。
  如果寄信失敗，紀錄照樣留著，管理員可以在後台寄件匣按「重寄」。

---

# 後台：設定「加班單要發給誰」

進 `/admin`，分頁「**員工與簽核路由**」。每位員工可設定兩種收件人：

- **簽核主管** — 收到通知信，而且**必須按「確認」**，員工才能送出正式加班申請。
- **副本收件人** — 只收信、不用簽核，可填任意 Email（HR、部門助理…）。
  「系統設定」裡另有一組**全公司預設副本收件人**，通常放 HR，每位員工可個別關掉。

表格會直接標出問題：`⚠ 未指定`、`⚠ 沒有 Email`、`⚠ 帳號已停用`、`未改初始密碼`。
「總覽」分頁會列出所有待補的設定。

其他分頁：

| 分頁 | 用途 |
|---|---|
| 加班紀錄 | 全公司紀錄，可依員工／狀態／日期／關鍵字篩選，**下載 CSV**（Excel 可直接開） |
| 寄件匣 | 系統產生的每封信都留著，可看內容、寄送狀態、失敗重寄 |
| 假日表 | 判斷「國定假日／例假（週日）／休息日（週六）」的依據，每年更新一次 |
| 系統設定 | 公司名稱、對外網址、預設副本收件人、寄信方式 |
| 操作紀錄 | 誰在什麼時候登入、登記、簽核、改設定 |

---

# 完整流程

1. 員工登入 → 填加班日期、時段、內容 → 按「蓋章送出」，伺服器當下寫入時間戳。
2. 系統依後台設定，寄通知信給**簽核主管**與**副本收件人**；主管信裡有一鍵簽核連結。
3. 主管點信裡的連結（不用登入），或登入後在「待我簽核」按確認／退回。
4. 主管確認後，員工才能送出「正式加班申請」，這時會再通知 HR 與主管。
5. 員工的登記頁會自動更新主管的簽核結果。

登記後不能修改，只能**作廢**（紀錄與時間戳都保留，只標記為已作廢）——這樣時間戳才有證據價值。
單筆上限 16 小時，用來擋「結束時間填得比開始時間早」的誤登記；真的跨夜請分兩筆。

---

# 同事連不上（本機模式）

**第一步：判斷請求有沒有到達你的電腦。**

```bash
tail -f ~/Downloads/overtime-server/data/server.log
```

請同事重新整理：**有跳出他的 IP** = 網路通、問題在頁面；**完全沒動靜** = 請求沒到達。

**第二步：請同事確認**

1. 網址完整：`http://<你的IP>:8080`（不是 `https://`、不能漏 `:8080`、不要打 `localhost`）
2. 他的 IP 要跟你在同一個網段（Mac：`ipconfig getifaddr en0`；Windows：`ipconfig`）
3. 用指令直接測（排除瀏覽器代理干擾）：
   - Mac：`curl -m 5 http://<你的IP>:8080/health`
   - Windows：`Test-NetConnection <你的IP> -Port 8080`

   指令通了但瀏覽器不通 → 是他的瀏覽器走公司代理，把內部網址也送去代理了。
   解法：瀏覽器代理設定把你的 IP 加進例外清單。

**第三步：你這端**

```bash
ps -ax | grep "[a]pp.py"                              # 伺服器還在跑嗎
lsof -nP -iTCP:8080 -sTCP:LISTEN                      # 要看到 *:8080
ipconfig getifaddr en0                                # IP 變了嗎
/usr/libexec/ApplicationFirewall/socketfilterfw --listapps | grep -A1 python
systemextensionsctl list | grep -i filter             # 企業安全軟體會擋連入
```

如果公司裝了端點防護（本機裝的是 WithSecure），它可能在 macOS 防火牆之外另擋一層連入連線，
這種情況要請 IT 放行，或者直接改用雲端部署（B 章節）——那就完全不需要對你的電腦開任何連入。

---

# 資料與備份

- **本機**：全部在 `data/overtime.db`。備份：`cp data/overtime.db ~/Desktop/backup-$(date +%Y%m%d).db`
- **雲端**：在 Neon 主控台可以看資料、也有自動備份（免費方案保留 7 天的還原點）

`data/` 已經在 `.gitignore` 裡，資料庫、初始密碼、憑證都不會被推上 GitHub。
雲端的密鑰（`DATABASE_URL`、`RESEND_API_KEY`）放 Vercel 環境變數，不寫在程式碼裡。

---

# 檔案結構

```
overtime-server/
├── 啟動伺服器.command   ← 本機雙擊啟動
├── app.py               本機啟動器（CLI、自簽憑證、--check-db）
├── api/index.py         Vercel serverless 進入點
├── server.py            HTTP 路由、權限、Cookie（本機與雲端共用）
├── api.py               員工端與簽核 API
├── api_admin.py         後台 API
├── auth.py              密碼雜湊（PBKDF2）、Session、登入失敗鎖定
├── db.py                資料庫層（SQLite / Postgres 雙後端）
├── dayutil.py           日別判斷與工時計算
├── mailer.py            組信、寄件匣、SMTP／Resend 寄送
├── public/              前端（雲端由 Vercel 直接託管）
│   ├── *.html
│   └── static/css, static/js
├── requirements.txt     雲端需要的 psycopg2
├── vercel.json          Vercel 路由設定
└── data/                本機資料（不進版控）
```
