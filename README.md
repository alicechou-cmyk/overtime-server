# 假日加班登記系統

# 🌐 已上線：https://overtime-server.vercel.app

這是正式使用的網址，全世界都連得到，不依賴任何人的電腦。

| 項目 | 內容 |
|---|---|
| 網址 | https://overtime-server.vercel.app |
| 管理員帳號 | `admin`（密碼是初次設定時你自己設的） |
| 後台 | https://overtime-server.vercel.app/admin |
| 主機 | Vercel（Hobby 免費方案，社團非商業用途適用） |
| 資料庫 | Neon Postgres 免費方案，與函式同區域（實測查詢延遲僅 +20ms） |
| 通知信 | Gmail SMTP，寄件人 alicechou@iwink.tw（使用 Google 應用程式密碼） |
| 時區 | 固定台灣時間（UTC+8），與雲端主機的 UTC 無關 |

## 上線後已驗證的項目

- 初次設定 → 建立管理員 → 登入 → 進後台
- 新增員工、設定簽核主管與副本收件人
- 首次登入強制改密碼（未改前所有功能都擋住）
- 登記加班（時間戳為台灣時間）、重複登記被擋、未簽核不能送正式申請
- 主管簽核、重複簽核被擋
- 送出正式申請、通知信進寄件匣
- 已送出正式申請的紀錄無法作廢（證據保護）
- 後台總覽統計、加班紀錄、CSV 匯出、寄件匣、操作紀錄、假日表
- Gmail 寄信實測成功（1.6 秒）

## 目前已知的一件待辦

程式碼裡已經修好、但**還沒部署上去**的一個問題：舊版是在資料庫交易內同步寄信，
萬一寄信卡住超過函式上限（60 秒），該筆加班登記會跟著回滾消失。

- 目前設定下**是安全的**：每次登記寄 2 封信（主管＋HR），最壞 50 秒 < 60 秒上限
- ⚠️ **在部署修正版之前，請不要讓單次通知超過 2 個收件人**
  （也就是「全公司預設副本收件人」＋每位員工的個別副本，加上主管，總數不要超過 2 封）
- 修正版已打包在 `~/Downloads/overtime-vercel.zip`，隨時可以部署

## 怎麼更新程式

目前是用 Vercel 的「拖放 zip」部署，每次更新都要手動上傳，而且會建立新專案。
建議之後把程式接上 GitHub（`git@github.com:alicechou-cmyk/overtime-server.git`
已建立，部署金鑰已產生在 `~/.ssh/overtime_deploy`，但 GitHub 需要密碼確認才能加入金鑰），
接上之後 `git push` 就會自動部署。

---


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

# B. 雲端（已完成，這裡只記錄怎麼維護）

部署已經做完了，這段留給以後需要維護時參考。

**目前的組成**

- 程式放在 Vercel（用「拖放 zip」的方式部署，專案名稱 `overtime-server`）
- 資料庫是 Vercel Marketplace 開的 Neon Postgres（`neon-cerulean-harbor`），
  `DATABASE_URL` 由整合自動注入專案環境變數，不用手動填
- 通知信走 Gmail SMTP（`smtp.gmail.com:587`、STARTTLS），
  帳號 `alicechou@iwink.tw`，密碼是 Google 應用程式密碼（存在系統的資料庫設定裡）

**怎麼更新程式**

1. 改完程式後在本機打包：

```bash
cd ~/Downloads/overtime-server && rm -f ~/Downloads/overtime-vercel.zip && zip -rq ~/Downloads/overtime-vercel.zip api api.py api_admin.py auth.py db.py dayutil.py mailer.py server.py wsgi.py public requirements.txt vercel.json -x "*__pycache__*" -x "*.pyc"
```

2. 到 <https://vercel.com/new> → 點「choose a **file**」→ 選那個 zip
3. 專案名稱填 `overtime-server`（若說名稱重複，要先把舊專案刪掉；
   **資料庫是獨立資源，刪專案不會刪資料**，但刪完要回 Storage 用
   「Connect Database」把 `neon-cerulean-harbor` 接回新專案，再 Redeploy）

**建議改成 GitHub 自動部署**（一次設定，之後 `git push` 就自動上線）

repo 已經建好：`git@github.com:alicechou-cmyk/overtime-server.git`，
部署金鑰也產生在 `~/.ssh/overtime_deploy`。缺的是 GitHub 要求輸入一次帳號密碼
（Confirm access）才能把金鑰加進 repo，那一步必須本人操作。
完成後在 Vercel 專案 Settings → Git 連上 repo 即可。

**只改設定不用重新部署**：改環境變數後，到 Deployments → 最新那筆 → `⋯` → Redeploy 就會生效。

## 方案與限制

- **Vercel Hobby 免費方案**：社團／個人非商業用途適用（Hobby 限制的是商業使用）
- **冷啟動**：一段時間沒人用之後，第一個請求會慢 1～3 秒，之後正常
- **函式執行上限 60 秒**（Fluid Compute 已啟用）
- **函式與資料庫都在美東**：從台灣連線實測約 0.3 秒，對社團用量足夠；
  資料庫就在函式旁邊（實測查詢只多 20ms），不需要調整

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

1. 員工登入 → 開始工作按「**開始**」，休息按「**暫停**」，回來按「**繼續**」，
   當天的工時會一直累計（每一次開始／暫停的時間都由伺服器記下）。
   一天做完按「**今天做完了，確認並送出總工時**」→ 確認每一段的明細、填工作內容 →
   送出，當日總工時成為一筆登記，伺服器當下寫入時間戳。
   - 忘了按開始：可用「手動補登時段」，照舊填開始、結束時間。
   - 忘了按暫停：計時跨過午夜會在 00:00 自動切開，前一天的工時歸前一天，
     畫面上會提醒「某天還有工時尚未送出」；按錯的段落送出前可以刪掉（操作紀錄會留下）。
   - 送出後作廢：那天的計時段落會回到「尚未送出」，修正後可以重新送出。
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
- **雲端**：在 Neon 主控台可以看資料、也有自動備份（免費方案保留 7 天的還原點）。
  Vercel 專案 → Storage → `neon-cerulean-harbor` → Query 可以直接下 SQL 看資料

`data/` 已經在 `.gitignore` 裡，資料庫、初始密碼、憑證都不會被推上 GitHub。
`DATABASE_URL` 由 Neon 整合自動注入 Vercel 環境變數，不寫在程式碼裡。
Gmail 應用程式密碼存在系統自己的資料庫（設定頁），畫面上只顯示為「已設定」。

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
