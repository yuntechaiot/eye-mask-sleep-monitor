# 深睡體驗營：公開 Streamlit 網站

本目錄已整理成可直接部署至 Streamlit Community Cloud 的單一網站：

- 受試者：開啟 `https://<app>.streamlit.app/?member=ycc00000001` 後填寫睡眠日誌。
- 研究人員：開啟 `https://<app>.streamlit.app/` 後用 DocterCloud 帳密登入健康數據監控室。
- 登入後可在側邊欄為 50 位會員生成個人連結與 QR Code。

公開填寫頁不顯示歷史紀錄、不匯出 CSV，也不允許刪除資料。Supabase 連線資訊只會存在 Streamlit 伺服器端。

## 重要檔案

| 檔案 | 用途 |
| --- | --- |
| `streamlit_app.py` | 公開網站入口，包含睡眠日誌表單與路由。 |
| `數據監控室.py` | DocterCloud 登入、會員搜尋、生理資料與睡眠指標。 |
| `database_schema_supabase.sql` | 資料表、RLS 與公開寫入權限。 |
| `requirements.txt` | Streamlit Community Cloud 所需 Python 套件與版本。 |
| `.streamlit/config.toml` | Streamlit 主題與伺服器設定。 |
| `.streamlit/secrets.toml.example` | 機密設定範本；不包含真實金鑰。 |

`睡眠日誌表單.html` 與 `start_public_site.py` 是舊版本機／區網用法，不會加入公開 GitHub 儲存庫。

## 1. 先保護 Supabase 資料

到 Supabase 專案的 **SQL Editor** 執行完整的 `database_schema_supabase.sql`。它會：

1. 保留／建立 `public.sleep_logs`。
2. 允許匿名受試者新增符合規格的日誌。
3. 移除匿名 `SELECT` 與 `DELETE`，避免公開網路上的個資洩漏或被刪除。

套用後，管理端必須在 Streamlit Secrets 使用 `SUPABASE_ADMIN_KEY` 讀取資料。請從 Supabase **Project Settings → API Keys** 取得 service-role key。該 key 具有高權限，絕對不可加入 GitHub、HTML 或傳給受試者。

## 2. 本機測試

在本目錄中執行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
```

編輯 `.streamlit/secrets.toml` 填入：

```toml
SUPABASE_URL = "https://<project-ref>.supabase.co"
SUPABASE_KEY = "<publishable-or-anon-key>"
SUPABASE_ADMIN_KEY = "<service-role-key>"
PUBLIC_APP_URL = "http://localhost:8501"
```

啟動：

```powershell
streamlit run streamlit_app.py
```

測試網址：

- 研究人員首頁：`http://localhost:8501/`
- 受試者表單：`http://localhost:8501/?member=ycc00000001`

## 3. 上傳 GitHub

在本目錄建立 Git 儲存庫後，只提交部署需要的檔案。`.gitignore` 已排除：

- Supabase 帳號與本機 `secrets.toml`。
- 受試者 SQL 資料。
- DocterCloud 與專案 PDF。
- 舊版純 HTML 與本機 HTTP Server。

公開儲存庫絕對不應包含帳密、service-role key 或受試者資料。

## 4. 部署 Streamlit Community Cloud

1. 到 [Streamlit Community Cloud](https://share.streamlit.io/) 並連結 GitHub。
2. 選擇 **Create app** → **Yup, I have an app**。
3. 選擇 GitHub 儲存庫與 `main` branch。
4. **Main file path** 填 `streamlit_app.py`。
5. 開啟 **Advanced settings**，在 **Secrets** 貼上真實設定：

```toml
SUPABASE_URL = "https://<project-ref>.supabase.co"
SUPABASE_KEY = "<publishable-or-anon-key>"
SUPABASE_ADMIN_KEY = "<service-role-key>"
PUBLIC_APP_URL = "https://<app-name>.streamlit.app"
DOCTERCLOUD_BASE_URL = "https://icare.docter.pro/back-end/app"
```

6. 按 **Deploy**。完成後就會得到 `https://<app-name>.streamlit.app` 公開外網網址。
7. 若部署時尚未知道最終 app name，可先部署，再到 App settings 將 `PUBLIC_APP_URL` 更新成實際網址。

## 安全說明

- DocterCloud 帳密只會送到既有的 DocterCloud API，登入 token 只存於當前 Streamlit session。
- 受試者頁面只能新增資料，不能讀取或刪除歷史紀錄。
- `SUPABASE_ADMIN_KEY` 僅能保存於 Streamlit Secrets。
- 公開表單仍可能遭自動化濫用；若將來要擴大為長期服務，建議再加上每位會員隨機 token 與伺服器端速率限制。
