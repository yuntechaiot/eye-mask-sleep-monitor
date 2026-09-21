import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import os
import time as system_time
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo  # 用於精確時區處理
from supabase import create_client, Client
from typing import Optional

from researcher_session import COOKIE_NAME, ResearcherSessionStore, cookie_script

# ==========================================
# 1. 設定與全域配置
# ==========================================
st.set_page_config(page_title="深睡體驗營", page_icon="🌙", layout="wide")


def get_setting(name: str, default: str = "") -> str:
    """先讀 Streamlit secrets，本機開發時再回退到環境變數。"""
    try:
        value = st.secrets.get(name)
    except (FileNotFoundError, KeyError):
        value = None
    return str(value or os.getenv(name, default)).strip()


BASE_URL = get_setting("DOCTERCLOUD_BASE_URL", "https://icare.docter.pro/back-end/app")
SUPABASE_URL = get_setting("SUPABASE_URL")
SUPABASE_KEY = get_setting("SUPABASE_KEY")
# 管理端讀取健康資料時應使用只存放在 Streamlit Secrets 的 service-role key。
# 若尚未設定，為了相容舊專案會暫時回退到 publishable key。
SUPABASE_ADMIN_KEY = get_setting("SUPABASE_ADMIN_KEY") or SUPABASE_KEY
TW_TZ = ZoneInfo("Asia/Taipei") # 定義台灣時區

@st.cache_resource
def get_supabase(url: str, key: str) -> Client:
    return create_client(url, key)


@st.cache_resource
def get_researcher_sessions() -> ResearcherSessionStore:
    return ResearcherSessionStore()


def ensure_session_state() -> None:
    """每個 Streamlit 使用者工作階段都要各自初始化。

    模組在伺服器進程中會被快取，因此不能只依賴 import 時執行一次。
    """
    if "token" not in st.session_state:
        st.session_state.token = None
    if "target_member" not in st.session_state:
        st.session_state.target_member = None
    if "researcher_session_id" not in st.session_state:
        st.session_state.researcher_session_id = None


def restore_researcher_session() -> None:
    """Restore a still-valid login after a browser refresh (new WebSocket)."""
    ensure_session_state()
    session_id = st.session_state.researcher_session_id
    if not session_id:
        try:
            session_id = st.context.cookies.get(COOKIE_NAME)
        except (AttributeError, RuntimeError):
            session_id = None
        if not isinstance(session_id, str):
            session_id = None

    session = get_researcher_sessions().get(session_id)
    if session:
        st.session_state.researcher_session_id = session_id
        st.session_state.token = session.token
        st.session_state.researcher_account = session.account
        st.session_state.researcher_expires_at = session.expires_at
        return

    if session_id:
        st.session_state.researcher_cookie_action = ("clear", None, False)
    st.session_state.token = None
    st.session_state.researcher_session_id = None
    st.session_state.target_member = None
    st.session_state.pop("researcher_account", None)
    st.session_state.pop("researcher_expires_at", None)


def render_researcher_cookie_action() -> None:
    """Write the opaque ID cookie from a first-party Streamlit HTML element."""
    action = st.session_state.pop("researcher_cookie_action", None)
    if action:
        operation, session_id, remember = action
        st.html(
            cookie_script(session_id if operation == "set" else None, remember=remember),
            unsafe_allow_javascript=True,
        )


def logout_researcher() -> None:
    get_researcher_sessions().revoke(st.session_state.get("researcher_session_id"))
    st.session_state.token = None
    st.session_state.researcher_session_id = None
    st.session_state.target_member = None
    st.session_state.pop("researcher_account", None)
    st.session_state.pop("researcher_expires_at", None)
    st.session_state.researcher_cookie_action = ("clear", None, False)


ensure_session_state()

# 圖表設定
CHARTS_CONFIG = [
    {"key": "SLEEP", "path": "/physiologic/sleep",          "type": "sleep",  "title": "🛌 睡眠階段"},
    {"key": "BP",    "path": "/physiologic/blood/pressure", "type": "bp",     "title": "🩸 血壓 (mmHg)"},
    {"key": "HR",    "path": "/physiologic/heart/rate",     "type": "single", "title": "❤️ 心率 (bpm)",     "val": "value"},
    {"key": "SPO2",  "path": "/physiologic/blood/oxygen",   "type": "single", "title": "🌬️ 血氧 (%)",       "val": "value"},
    {"key": "TEMP",  "path": "/physiologic/body/temperature","type": "single", "title": "🌡️ 體溫 (°C)",       "val": "value"},
    {"key": "HRV_LFHF","path": "/physiologic/hrv", "type": "single", "title": "⚖️ HRV-(LF/HF)", "val": "lf_hf"},
    {"key": "HRV_F",   "path": "/physiologic/hrv", "type": "single", "title": "😫 HRV - Fatigue", "val": "fatigue"},
    {"key": "HRV_TP",  "path": "/physiologic/hrv", "type": "single", "title": "🔋 HRV - TP",       "val": "tp"},
    {"key": "HRV_LF",  "path": "/physiologic/hrv", "type": "single", "title": "🐢 HRV - LF",         "val": "lf"},
    {"key": "HRV_HF",  "path": "/physiologic/hrv", "type": "single", "title": "⚡ HRV - HF",         "val": "hf"},
    {"key": "HRV_VLF", "path": "/physiologic/hrv", "type": "single", "title": "💤 HRV - VLF",      "val": "vlf"}
]

SLEEP_CONFIG = [c for c in CHARTS_CONFIG if c["type"] == "sleep"][0]
PHYSIO_CONFIGS = [c for c in CHARTS_CONFIG if c["type"] != "sleep"]

# 睡眠狀態名稱對照表
STAGE_MAP = {
    0: {"name": "清醒", "color": "#ef4444"},
    3: {"name": "快速眼動", "color": "#8b5cf6"},
    1: {"name": "淺睡", "color": "#3b82f6"},
    2: {"name": "深睡", "color": "#1d4ed8"},
    4: {"name": "零星睡眠", "color": "#9ca3af"}
}

# ==========================================
# 2. 輔助功能
# ==========================================
def login(account, password, remember=False):
    try:
        res = requests.post(
            f"{BASE_URL}/login/researcher",
            data={"account": account, "password": password},
            timeout=20,
        )
        if res.status_code == 200 and res.json().get("success"):
            token = res.json()["data"]["access_token"]
            get_researcher_sessions().revoke(st.session_state.get("researcher_session_id"))
            session_id, session = get_researcher_sessions().create(token, account)
            st.session_state.token = token
            st.session_state.researcher_session_id = session_id
            st.session_state.researcher_account = account
            st.session_state.researcher_expires_at = session.expires_at
            st.session_state.researcher_cookie_action = ("set", session_id, remember)
            st.success("✅ 登入成功！")
            return True
        else:
            st.error("❌ 登入失敗，請檢查帳號密碼")
    except Exception as e:
        st.error(f"❌ 連線錯誤: {e}")
    return False

def search_member(keyword):
    if not st.session_state.token:
        return []
    
    all_members = []
    page = 1
    headers = {"Authorization": f"Bearer {st.session_state.token}"}
    
    try:
        while page <= 10: 
            params = {"keyword": keyword, "page": page}
            res = requests.get(f"{BASE_URL}/user", headers=headers, params=params, timeout=20)
            data = res.json()
            
            if data.get("success"):
                member_list = data["data"]["list"]
                if not member_list:
                    break
                all_members.extend(member_list)
                page += 1
            else:
                break
    except Exception as e:
        pass
        
    return all_members

def fetch_data(member_id, start_ts, end_ts, keys=None):
    data_store = {}
    headers = {"Authorization": f"Bearer {st.session_state.token}"}
    params = {"user_id": member_id, "start_date": start_ts, "end_date": end_ts}
    
    api_cache = {}

    for config in CHARTS_CONFIG:
        if keys is not None and config["key"] not in keys:
            continue

        path = config['path']
        if path in api_cache:
            data_store[config["key"]] = api_cache[path]
            continue

        url = f"{BASE_URL}{path}"
        try:
            res = requests.get(url, headers=headers, params=params, timeout=30)
            res_json = res.json()
            if res_json.get("success") and res_json.get("data"):
                data = res_json["data"]
            else:
                data = []
        except:
            data = []
        
        api_cache[path] = data
        data_store[config["key"]] = data

    return data_store

# 🌟 睡眠資料去重疊與清洗邏輯
def clean_sleep_records(raw_sleep_data):
    """清除 API 傳回的重疊/子集睡眠紀錄，只保留最完整的那筆"""
    if not raw_sleep_data:
        return []
        
    # 修補 API 可能的拼字錯誤 (emd_time) 確保有 end_time 欄位
    for r in raw_sleep_data:
        if 'end_time' not in r and 'emd_time' in r:
            r['end_time'] = r['emd_time']

    df = pd.DataFrame(raw_sleep_data)
    
    # 防呆：如果必要欄位缺失，原樣退回
    if 'start_time' not in df.columns or 'end_time' not in df.columns:
        return raw_sleep_data

    # 先用舊邏輯處理「start_time 完全相同」的子集情況
    idx_to_keep = df.groupby('start_time')['end_time'].idxmax()
    df = df.loc[idx_to_keep].sort_values('start_time').reset_index(drop=True)

    # 再用「時間區間重疊」邏輯去重：時間重疊的多筆紀錄只保留時長最長的那一筆
    kept_rows = []
    for _, row in df.iterrows():
        merged = False
        for kept in kept_rows:
            # 判斷是否重疊：A.start < B.end 且 B.start < A.end
            if row['start_time'] < kept['end_time'] and kept['start_time'] < row['end_time']:
                # 重疊：保留時長較長的那一筆
                row_dur = row['end_time'] - row['start_time']
                kept_dur = kept['end_time'] - kept['start_time']
                if row_dur > kept_dur:
                    kept.update(row.to_dict())
                merged = True
                break
        if not merged:
            kept_rows.append(row.to_dict())

    cleaned_df = pd.DataFrame(kept_rows).sort_values('start_time')
    return cleaned_df.to_dict('records')

# ==========================================
# Supabase 輔助功能
# ==========================================
def fetch_supabase_sleep_record(member_id: str, sleep_date: str) -> Optional[dict]:
    """
    從 Supabase 撈取指定會員、指定日期的躺床/起床紀錄。
    資料表欄位：member_id, date (text "YYYY-MM-DD"), sleep_time (text "HH:MM"), wake_time (text "HH:MM")
    回傳 dict 或 None（查無資料時）。
    """
    
    url = SUPABASE_URL
    key = SUPABASE_ADMIN_KEY
    if not url or not key:
        return None
    try:
        supabase = get_supabase(url, key)
        resp = (
            supabase.table("sleep_logs")#sleep_records
            .select("sleep_time,wake_time,sleep_time_ampm")
            .eq("member_id", member_id)
            .eq("date", sleep_date)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]
    
    except Exception as e:
        st.warning(f"⚠️ Supabase 查詢失敗: {e}")
    return None

def _parse_dt(s, ref_date=""):
    """
    把純時間字串(HH:MM)或完整日期時間字串轉成 datetime。
    ref_date: "YYYY-MM-DD"，當 s 只有時間部分時用於拼接。
    """
    if not s:
        return None

    s = s.strip()

    # 純時間格式 "HH:MM" 或 "HH:MM:SS" → 拼上日期
    if len(s) <= 8 and ":" in s and "-" not in s:
        if len(s) == 5:   # "HH:MM"
            s = f"{ref_date}T{s}:00"
        else:              # "HH:MM:SS"
            s = f"{ref_date}T{s}"

    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TW_TZ)
            return dt
        except ValueError:
            continue
    return None
def calc_sleep_metrics(
    supabase_bed_time_str: Optional[str],
    supabase_wake_time_str: Optional[str],
    sleep_date_str: str,          # ← 新增：用於把純時間字串拼成完整 datetime
    watch_start_ts: Optional[int],
    watch_end_ts: Optional[int],
    watch_awake_min: Optional[float],
    watch_total_sleep_min: Optional[float],
    detail_obj: list,
    bed_time_ampm: Optional[str] = None,
) -> dict:
    """
    計算所有睡眠衍生指標，回傳 dict。
    所有輸入均可為 None；缺資料的欄位回傳 None。

    Supabase 的 sleep_time / wake_time 可能只有 "HH:MM" 格式，
    此函式會自動拼上 sleep_date_str 還原成完整 datetime。
    跨夜（wake < bed）會自動 +1 天修正。
    """
    result = {
        "整體躺床時間(分)": None,
        "整體入睡時間(分)": None,
        "睡眠潛伏期(分)": None,
        "夜間醒來次數(次)": None,
        "入睡後醒來時間(分)": None,
        "賴床時間(分)": None,
        "睡眠效率(%)": None,
    }

    
    

    bed_dt  = _parse_dt(supabase_bed_time_str,  sleep_date_str)
    
    wake_dt = _parse_dt(supabase_wake_time_str, sleep_date_str)
    wake_dt = _parse_dt(supabase_wake_time_str, sleep_date_str)

    # ── 1. 整體躺床時間（處理跨夜：wake < bed 時 +1 天）──────────────
    if bed_dt and wake_dt:
        delta = wake_dt - bed_dt
        if delta.total_seconds() < 0:   # 跨夜修正（例如 23:00 → 05:41）
            wake_dt += timedelta(days=1)
            delta = wake_dt - bed_dt
        result["整體躺床時間(分)"] = round(delta.total_seconds() / 60, 1)

    # ── 2. 整體入睡時間 = 手錶睡眠結束 − 手錶睡眠開始 ───────────
    if watch_start_ts and watch_end_ts:
        result["整體入睡時間(分)"] = round((watch_end_ts - watch_start_ts) / 60, 1)

    # ── 3. 睡眠潛伏期 = 手錶 m_start − Supabase sleep_time ───────────
    if bed_dt and watch_start_ts:
        watch_start_dt = datetime.fromtimestamp(watch_start_ts, tz=TW_TZ)
        latency = (watch_start_dt - bed_dt).total_seconds() / 60
        result["睡眠潛伏期(分)"] = round(max(latency, 0), 1)

    # ── 3. 夜間醒來次數 = detail_obj 中 type==0 的段數 ───────────────
    if detail_obj:
        result["夜間醒來次數(次)"] = sum(
            1 for d in detail_obj if d.get("type") == 0
        )

    # ── 4. 入睡後醒來時間 = 手錶 awake 欄位 ──────────────────────────
    if watch_awake_min is not None:
        result["入睡後醒來時間(分)"] = round(watch_awake_min, 1)

    # ── 5. 賴床時間 = Supabase 起床時間 − 手錶睡眠結束時間 ───────────
    if wake_dt and watch_end_ts:
        watch_end_dt = datetime.fromtimestamp(watch_end_ts, tz=TW_TZ)
        laziness = (wake_dt - watch_end_dt).total_seconds() / 60
        result["賴床時間(分)"] = round(max(laziness, 0), 1)

    # ── 6. 睡眠效率 = 整體入睡時間 / 整體躺床時間 × 100% ────────────
    if watch_total_sleep_min and result["整體躺床時間(分)"]:
        efficiency = (watch_total_sleep_min / result["整體躺床時間(分)"]) * 100
        result["睡眠效率(%)"] = round(min(efficiency, 100.0), 1)

    return result

# ==========================================
# 3. 繪圖功能
# ==========================================
def create_clean_sleep_chart(df):
    """乾淨的睡眠階段圖 (不重疊線條，數值放於Hover提示)"""
    fig = go.Figure()

    if df.empty:
        fig.add_annotation(text="無睡眠數據", showarrow=False, font=dict(size=20, color="gray"))
        fig.update_layout(xaxis_visible=False, yaxis_visible=False, height=300)
        return fig

    df['start_dt'] = pd.to_datetime(df['start_time'], unit='s', utc=True).dt.tz_convert(TW_TZ)
    df['end_dt'] = pd.to_datetime(df['end_time'], unit='s', utc=True).dt.tz_convert(TW_TZ)
    df['duration_ms'] = (df['end_dt'] - df['start_dt']).dt.total_seconds() * 1000

    for stype, stage_info in STAGE_MAP.items():
        mask = df['sleep_type'] == stype
        sub_df = df[mask]
        
        if not sub_df.empty:
            def build_hover(r):
                hr_text = f"{r['avg_hr']:.1f} bpm" if pd.notna(r['avg_hr']) else "無資料"
                bp_text = f"{r['avg_sys']:.0f}/{r['avg_dia']:.0f} mmHg" if pd.notna(r['avg_sys']) and pd.notna(r['avg_dia']) else "無資料"
                return (
                    f"<b>{stage_info['name']}</b><br>"
                    f"時間: {r['start_dt'].strftime('%m/%d %H:%M')} - {r['end_dt'].strftime('%H:%M')}<br>"
                    f"時長: {int((r['end_dt'] - r['start_dt']).total_seconds() / 60)} 分鐘<br>"
                    f"---<br>"
                    f"❤️ 平均心率: {hr_text}<br>"
                    f"🩸 平均血壓: {bp_text}"
                )
            
            hover_texts = sub_df.apply(build_hover, axis=1)
            
            fig.add_trace(
                go.Bar(
                    base=sub_df['start_dt'],
                    x=sub_df['duration_ms'],
                    y=["睡眠進度"] * len(sub_df),
                    orientation='h',
                    marker=dict(color=stage_info['color'], line=dict(color='white', width=0.5)),
                    name=stage_info["name"],
                    hovertext=hover_texts,
                    hoverinfo="text",
                    width=0.4,
                    opacity=0.9
                )
            )

    fig.update_layout(
        title=dict(text="🛌 睡眠階段分布 (滑鼠移入查看生理數值)", font=dict(size=18)),
        xaxis=dict(title="時間", type="date", tickformat="%m/%d\n%H:%M"),
        barmode='overlay', 
        hovermode="closest",
        height=250, 
        margin=dict(l=20, r=20, t=50, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.1, xanchor="right", x=1)
    )
    return fig

def create_combined_physio_chart(data_store, selected_configs, interpolate=False):
    """綜合生理數據趨勢圖"""
    fig = go.Figure()
    has_data = False

    for config in selected_configs:
        raw_records = data_store.get(config["key"], [])
        if not raw_records: continue

        processed_records = []
        for r in raw_records:
            new_r = r.copy()
            nested_data = r.get('data')
            
            if config['type'] == 'bp':
                sys = r.get('sys') or r.get('systolic') or r.get('sbp')
                dia = r.get('dia') or r.get('diastolic') or r.get('dbp')
                if (sys is None or dia is None) and isinstance(nested_data, dict):
                    if sys is None: sys = nested_data.get('sys') or nested_data.get('systolic') or nested_data.get('sbp')
                    if dia is None: dia = nested_data.get('dia') or nested_data.get('diastolic') or nested_data.get('dbp')
                new_r['sys'] = sys
                new_r['dia'] = dia
            else:
                new_r['hf'] = None; new_r['lf'] = None; new_r['lf_hf'] = None
                if isinstance(nested_data, dict):
                    new_r.update(nested_data)
                    hf = nested_data.get('hf')
                    lf = nested_data.get('lf')
                    ratio = nested_data.get('lf_hf')
                    if ratio is None and lf is not None and hf is not None and hf != 0:
                        ratio = lf / hf
                    new_r['lf_hf'] = ratio
            processed_records.append(new_r)

        df = pd.DataFrame(processed_records)
        if df.empty or 'occur_time' not in df.columns:
            continue
            
        df['dt'] = pd.to_datetime(df['occur_time'], unit='s', utc=True).dt.tz_convert(TW_TZ)
        df = df.sort_values('dt')
        
        mode = 'lines+markers' if interpolate else 'markers'

        if config["type"] == "bp":
            df['sys'] = pd.to_numeric(df['sys'], errors='coerce')
            df['dia'] = pd.to_numeric(df['dia'], errors='coerce')
            df_clean = df.dropna(subset=['sys', 'dia'], how='all')
            if not df_clean.empty:
                has_data = True
                fig.add_trace(go.Scatter(x=df_clean['dt'], y=df_clean['sys'], mode=mode, name='收縮壓 (Sys)'))
                fig.add_trace(go.Scatter(x=df_clean['dt'], y=df_clean['dia'], mode=mode, name='舒張壓 (Dia)'))
                
        elif config["type"] == "single":
            val_col = config.get("val")
            if val_col in df.columns:
                df[val_col] = pd.to_numeric(df[val_col], errors='coerce')
                df_clean = df.dropna(subset=[val_col])
                if not df_clean.empty:
                    has_data = True
                    fig.add_trace(go.Scatter(x=df_clean['dt'], y=df_clean[val_col], mode=mode, name=config["title"]))

    fig.update_layout(
        title=dict(text="📈 綜合生理數據趨勢圖", font=dict(size=20)),
        xaxis=dict(title="時間", type="date", tickformat="%m/%d\n%H:%M"),
        yaxis_title="數值",
        hovermode="x unified",
        height=450,
        margin=dict(l=20, r=20, t=50, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return fig

# ==========================================
# 4. 儀表板區塊 (資料處理核心)
# ==========================================
def get_dashboard_fragment(run_every_seconds):
    
    @st.fragment(run_every=run_every_seconds)
    def dashboard_section(member_id, member_account, dt_start, dt_end, interpolate, selected_physio_titles):
        ts_start = int(dt_start.timestamp())
        ts_end = int(dt_end.timestamp())
        
        
        update_time = datetime.now(TW_TZ).strftime('%H:%M:%S')
        st.caption(f"⏱️ 數據最後更新於: {update_time}")

        data_store = fetch_data(member_id, ts_start, ts_end)
        
        selected_configs = [c for c in PHYSIO_CONFIGS if c["title"] in selected_physio_titles]
        if selected_configs:
            fig_combined = create_combined_physio_chart(data_store, selected_configs, interpolate=interpolate)
            st.plotly_chart(fig_combined, use_container_width=True, key="chart_combined")
        else:
            st.warning("👈 請從左側選單勾選要顯示的生理數據。")
            
        st.divider()

        # 預先處理 HR 與 BP 用於計算各區段平均值
        raw_hr = data_store.get("HR", [])
        df_hr = pd.DataFrame([r for r in raw_hr if 'occur_time' in r and 'value' in r])
        if not df_hr.empty:
            df_hr['occur_time'] = pd.to_numeric(df_hr['occur_time'], errors='coerce')
            df_hr['value'] = pd.to_numeric(df_hr['value'], errors='coerce')
            df_hr = df_hr.dropna()

        raw_bp = data_store.get("BP", [])
        bp_records = []
        for r in raw_bp:
            sys = r.get('sys') or r.get('systolic') or r.get('sbp')
            dia = r.get('dia') or r.get('diastolic') or r.get('dbp')
            nested_data = r.get('data')
            if (sys is None or dia is None) and isinstance(nested_data, dict):
                if sys is None: sys = nested_data.get('sys') or nested_data.get('systolic') or nested_data.get('sbp')
                if dia is None: dia = nested_data.get('dia') or nested_data.get('diastolic') or nested_data.get('dbp')
            if r.get('occur_time') and sys and dia:
                bp_records.append({'occur_time': r.get('occur_time'), 'sys': sys, 'dia': dia})
        df_bp = pd.DataFrame(bp_records)
        if not df_bp.empty:
            df_bp['occur_time'] = pd.to_numeric(df_bp['occur_time'], errors='coerce')
            df_bp['sys'] = pd.to_numeric(df_bp['sys'], errors='coerce')
            df_bp['dia'] = pd.to_numeric(df_bp['dia'], errors='coerce')
            df_bp = df_bp.dropna()

        # 套用過濾重疊資料的邏輯
        raw_sleep = data_store.get(SLEEP_CONFIG["key"], [])
        raw_sleep = clean_sleep_records(raw_sleep) 
        
        processed_sleep = []
        sleep_summaries = [] 
        seen_sleep = set()
        
        if raw_sleep:
            for r in raw_sleep:
                details = r.get('detail_obj', [])
                m_start = r.get('start_time')
                m_end = r.get('end_time')

                if details and (not m_start or not m_end):
                    valid_d = [d for d in details if d.get('start_time') and d.get('end_time')]
                    if valid_d:
                        m_start = min(d['start_time'] for d in valid_d)
                        m_end = max(d['end_time'] for d in valid_d)

                if m_start and m_end:
                    time_key = (m_start, m_end)
                    if time_key in seen_sleep: continue
                    seen_sleep.add(time_key)

                    awake_mins, rem_mins, light_mins, deep_mins = 0, 0, 0, 0
                    sorted_details = sorted([d for d in details if d.get('start_time') and d.get('end_time')], key=lambda x: x['start_time'])

                    for d in sorted_details:
                        d_start, d_end = d['start_time'], d['end_time']
                        d_type = d.get('type')
                        duration = (d_end - d_start) / 60.0
                        
                        if d_type == 0: awake_mins += duration
                        elif d_type == 1: light_mins += duration
                        elif d_type == 2: deep_mins += duration
                        elif d_type == 3: rem_mins += duration

                        avg_hr, avg_sys, avg_dia = None, None, None
                        if not df_hr.empty:
                            seg_hr = df_hr[(df_hr['occur_time'] >= d_start) & (df_hr['occur_time'] < d_end)]
                            if not seg_hr.empty: avg_hr = seg_hr['value'].mean()
                        if not df_bp.empty:
                            seg_bp = df_bp[(df_bp['occur_time'] >= d_start) & (df_bp['occur_time'] < d_end)]
                            if not seg_bp.empty: 
                                avg_sys = seg_bp['sys'].mean()
                                avg_dia = seg_bp['dia'].mean()

                        processed_sleep.append({
                            'start_time': d_start, 'end_time': d_end, 'sleep_type': d_type,
                            'avg_hr': avg_hr, 'avg_sys': avg_sys, 'avg_dia': avg_dia
                        })

                    dt_s = pd.to_datetime(m_start, unit='s', utc=True).tz_convert(TW_TZ).strftime('%m/%d %H:%M')
                    dt_e = pd.to_datetime(m_end, unit='s', utc=True).tz_convert(TW_TZ).strftime('%m/%d %H:%M')

                    # 手錶 API 原始欄位
                    watch_awake_api = r.get('data', {}).get('awake')
                    total_sleep_min = (light_mins + deep_mins + rem_mins)

                    # 從 Supabase 撈躺床/起床時間
                    # 注意：睡眠日期以「入睡當天」為準（手錶 start_time 對應的台灣日期）
                    # sleep_date_str = pd.to_datetime(m_start, unit='s', utc=True).tz_convert(TW_TZ).strftime('%Y-%m-%d')
                    # sb_record = fetch_supabase_sleep_record(member_account, sleep_date_str)
                    # st.write(f"DEBUG member_account={member_account!r}, sleep_date_str={sleep_date_str!r}, sb_record={sb_record!r}")
                    # sb_bed_time  = sb_record.get("sleep_time") if sb_record else None
                    # sb_wake_time = sb_record.get("wake_time")  if sb_record else None
                    m_start_dt = pd.to_datetime(m_start, unit='s', utc=True).tz_convert(TW_TZ)

                    if m_start_dt.hour < 12:
                        # 凌晨入睡：優先查「前一天」，查不到才回頭查當天
                        primary_date_str   = (m_start_dt - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                        fallback_date_str  = m_start_dt.strftime('%Y-%m-%d')
                    else:
                        # 晚上入睡：優先查「當天」，查不到才往前一天
                        primary_date_str   = m_start_dt.strftime('%Y-%m-%d')
                        fallback_date_str  = (m_start_dt - pd.Timedelta(days=1)).strftime('%Y-%m-%d')

                    sleep_date_str = primary_date_str
                    sb_record = fetch_supabase_sleep_record(member_account, sleep_date_str)
                    if not sb_record:
                        sb_record_fb = fetch_supabase_sleep_record(member_account, fallback_date_str)
                        if sb_record_fb:
                            sb_record = sb_record_fb
                            sleep_date_str = fallback_date_str
                    # 防呆：驗證 bed_dt 跟手錶入睡時間差距是否在合理範圍內（8小時內）
                    if sb_record and sb_record.get("sleep_time"):
                        _bed_check = _parse_dt(sb_record["sleep_time"], sleep_date_str)
                        if _bed_check:
                            _gap_hours = abs((m_start_dt - _bed_check).total_seconds()) / 3600
                            if _gap_hours > 8:
                                sb_record = None  # 差距過大，視為配對失敗，放棄這筆紀錄

                    
                    sb_bed_time     = sb_record.get("sleep_time")      if sb_record else None
                    sb_wake_time    = sb_record.get("wake_time")       if sb_record else None
                    sb_bed_time_ampm = sb_record.get("sleep_time_ampm") if sb_record else None
                    
                    # 計算所有衍生指標（傳入 sleep_date_str 供純時間字串拼接）
                    metrics = calc_sleep_metrics(
                        supabase_bed_time_str  = sb_bed_time,
                        supabase_wake_time_str = sb_wake_time,
                        sleep_date_str         = sleep_date_str,   # ← 新增
                        watch_start_ts         = m_start,
                        watch_end_ts           = m_end,
                        watch_awake_min        = watch_awake_api,
                        watch_total_sleep_min  = total_sleep_min,
                        detail_obj             = sorted_details,
                        bed_time_ampm          = sb_bed_time_ampm,  
                    )

                    def _fmt(v, unit=""):
                        return f"`{v}{unit}`" if v is not None else "`N/A`"

                    summary = (
                        f"**時間:** `{dt_s}` ~ `{dt_e}`\n\n"
                        f"**清醒:** `{awake_mins:.0f}` 分 | **快速眼動:** `{rem_mins:.0f}` 分 | "
                        f"**淺睡:** `{light_mins:.0f}` 分 | **深睡:** `{deep_mins:.0f}` 分\n\n"
                        f"---\n\n"
                        f"🛏️ **整體躺床時間:** {_fmt(metrics['整體躺床時間(分)'], ' 分')}　"
                        f"😴 **整體入睡時間:** {_fmt(metrics['整體入睡時間(分)'], ' 分')}　"
                        f"⏱️ **睡眠潛伏期:** {_fmt(metrics['睡眠潛伏期(分)'], ' 分')}　"
                        f"🌙 **夜間醒來次數:** {_fmt(metrics['夜間醒來次數(次)'], ' 次')}\n\n"
                        f"😴 **入睡後醒來時間:** {_fmt(metrics['入睡後醒來時間(分)'], ' 分')}　"
                        f"🥱 **賴床時間:** {_fmt(metrics['賴床時間(分)'], ' 分')}　"
                        f"📊 **睡眠效率:** {_fmt(metrics['睡眠效率(%)'], ' %')}"
                    )
                    sleep_summaries.append(summary)

        if sleep_summaries:
            st.subheader("🛌 睡眠總覽與特定時段分析")
            for s in sleep_summaries: st.info(s)
            
            df_sleep = pd.DataFrame(processed_sleep)
            if not df_sleep.empty:
                st.plotly_chart(create_clean_sleep_chart(df_sleep), use_container_width=True, key="chart_sleep")

            st.markdown("### 🔍 點選下方時段查看細部生理數值")
            if not df_sleep.empty:
                stage_options = {}
                for idx, row in df_sleep.iterrows():
                    s_dt = pd.to_datetime(row['start_time'], unit='s', utc=True).tz_convert(TW_TZ).strftime('%m/%d %H:%M')
                    e_dt = pd.to_datetime(row['end_time'], unit='s', utc=True).tz_convert(TW_TZ).strftime('%H:%M')
                    duration = int((row['end_time'] - row['start_time']) / 60)
                    s_name = STAGE_MAP.get(row['sleep_type'], {}).get('name', '未知')
                    label = f"{idx+1}. [{s_name}] {s_dt} ~ {e_dt} (共 {duration} 分鐘)"
                    stage_options[label] = row

                selected_label = st.selectbox("請選擇一個睡眠區段：", list(stage_options.keys()))
                if selected_label:
                    selected_data = stage_options[selected_label]
                    st.markdown("### 📈 該睡眠區段生理趨勢")
                    d_start, d_end = selected_data['start_time'], selected_data['end_time']

                    # 針對睡眠區段重新抓取 BP/HR 數據
                    segment_data = fetch_data(member_id, d_start, d_end, keys=["BP", "HR"])

                    fig_segment = go.Figure()
                    has_seg_data = False
                    segment_configs = [c for c in PHYSIO_CONFIGS if c["key"] in ["BP", "HR"]]

                    for config in segment_configs:
                        raw_records = segment_data.get(config["key"], [])
                        processed = []
                        for r in raw_records:
                            t = r.get('occur_time')
                            if not t or t < d_start or t > d_end: continue
                            new_r = r.copy()
                            nested_data = r.get("data")
                            if config["type"] == "bp":
                                sys = r.get("sys") or r.get("systolic") or r.get("sbp")
                                dia = r.get("dia") or r.get("diastolic") or r.get("dbp")
                                if (sys is None or dia is None) and isinstance(nested_data, dict):
                                    sys = sys or nested_data.get("sys")
                                    dia = dia or nested_data.get("dia")
                                new_r["sys"], new_r["dia"] = sys, dia
                            elif isinstance(nested_data, dict):
                                new_r.update(nested_data)
                            processed.append(new_r)

                        df_seg = pd.DataFrame(processed)
                        if not df_seg.empty:
                            df_seg["dt"] = pd.to_datetime(df_seg["occur_time"], unit="s", utc=True).dt.tz_convert(TW_TZ)
                            df_seg = df_seg.sort_values("dt")
                            if config["type"] == "bp":
                                df_seg["sys"] = pd.to_numeric(df_seg["sys"], errors="coerce")
                                df_seg["dia"] = pd.to_numeric(df_seg["dia"], errors="coerce")
                                df_seg = df_seg.dropna(subset=["sys", "dia"], how="all")
                                if not df_seg.empty:
                                    has_seg_data = True
                                    fig_segment.add_trace(go.Scatter(x=df_seg["dt"], y=df_seg["sys"], mode="lines+markers", name="收縮壓"))
                                    fig_segment.add_trace(go.Scatter(x=df_seg["dt"], y=df_seg["dia"], mode="lines+markers", name="舒張壓"))
                            elif config["type"] == "single":
                                val_col = config.get("val")
                                if val_col in df_seg.columns:
                                    df_seg[val_col] = pd.to_numeric(df_seg[val_col], errors="coerce")
                                    df_seg = df_seg.dropna(subset=[val_col])
                                    if not df_seg.empty:
                                        has_seg_data = True
                                        fig_segment.add_trace(go.Scatter(x=df_seg["dt"], y=df_seg[val_col], mode="lines+markers", name=config["title"]))

                    if has_seg_data:
                        fig_segment.update_layout(title="📊 睡眠區段生理趨勢圖", xaxis=dict(title="時間", type="date"), hovermode="x unified", height=400)
                        st.plotly_chart(fig_segment, use_container_width=True)
                    else: st.warning("此睡眠區段沒有可用生理數據")
    return dashboard_section

# ==========================================
# 5. 主程式
# ==========================================
def main():
    ensure_session_state()
    restore_researcher_session()
    render_researcher_cookie_action()
    st.sidebar.title("🩺 登入")

    if not st.session_state.token:
        with st.sidebar.form("login_form"):
            user, pwd = st.text_input("帳號"), st.text_input("密碼", type="password")
            remember = st.checkbox("記住我（30 分鐘內免重新輸入）")
            if st.form_submit_button("登入"):
                if login(user, pwd, remember): st.rerun()
        st.sidebar.caption("重新整理後可在 30 分鐘內保持登入；勾選後，關閉再開啟瀏覽器也可在時限內免輸入。密碼不會被儲存。")
    else:
        st.sidebar.success("✅ 已登入")
        remaining_minutes = max(1, int((st.session_state.researcher_expires_at - system_time.time() + 59) // 60))
        st.sidebar.caption(f"登入狀態約剩 {remaining_minutes} 分鐘")
        if st.sidebar.button("登出"):
            logout_researcher()
            st.rerun()


    if st.session_state.token:
        st.sidebar.divider()
        st.sidebar.header("🔍 搜尋")
        search_kw = st.sidebar.text_input("輸入帳號")
        if search_kw:
            results = search_member(search_kw)
            if results:
                options = {f"{u['name']} ({u.get('account', '')})": u for u in results}
                selected = st.sidebar.selectbox("選擇會員", list(options.keys()))
                st.session_state.target_member = options[selected]

        st.sidebar.divider()
        st.sidebar.header("📅 時間區間設定")
        
        col1, col2 = st.sidebar.columns(2)
        with col1:
            start_date = st.date_input("開始日期", value="today")
            start_time = st.time_input("開始時間", time(12, 0))
        with col2:
            end_date = st.date_input("結束日期", value="today")
            end_time = st.time_input("結束時間", value="now")
            
        dt_start = datetime.combine(start_date, start_time).replace(tzinfo=TW_TZ)
        dt_end = datetime.combine(end_date, end_time).replace(tzinfo=TW_TZ)

        st.sidebar.divider()
        st.sidebar.header("⚙️ 顯示設定")
        interpolate = st.sidebar.checkbox("顯示趨勢平滑線", value=True)
        auto_refresh = st.sidebar.checkbox("啟用自動刷新 (每1秒)", value=False)
        
        st.title("🏥 健康監控")
        
        if st.session_state.target_member:
            member = st.session_state.target_member
            st.info(f"正在監控: **{member['name']}** | 區間: {dt_start.strftime('%Y/%m/%d %H:%M')} ~ {dt_end.strftime('%Y/%m/%d %H:%M')}")
            
            physio_titles = [c["title"] for c in PHYSIO_CONFIGS]
            selected_physio_titles = st.multiselect("📈 選擇生理趨勢圖:", options=physio_titles, default=["❤️ 心率 (bpm)", "🩸 血壓 (mmHg)"])
            
            dashboard = get_dashboard_fragment(run_every_seconds=1 if auto_refresh else None)
            dashboard(member['id'], member.get('identifier', ''), dt_start, dt_end, interpolate, selected_physio_titles)
            
            if not auto_refresh and st.button("🔄 手動刷新"): st.rerun()
        else: st.warning("👈 請先從左側選擇要觀看的會員")

if __name__ == "__main__":
    main()
