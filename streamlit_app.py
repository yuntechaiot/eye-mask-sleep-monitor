"""
深睡體驗營公開入口。

同一個 Streamlit 網址提供：
1. ?member=ycc00000001 形式的受試者睡眠日誌表單。
2. 未帶 member 參數時的研究人員登入與健康數據監控室。

Supabase 連線資訊只從 Streamlit Secrets / 環境變數讀取，
不會傳送到受試者的瀏覽器。
"""

from __future__ import annotations

import importlib


import io
import re
from datetime import datetime, time, timedelta

import pandas as pd
import qrcode
import streamlit as st


dashboard = importlib.import_module("數據監控室")

MEMBER_PATTERN = re.compile(r"^ycc\d{8}$")
MEMBERS = [f"ycc{i:08d}" for i in range(1, 51)]

SLEEP_LOG_SELECT = ",".join(
    [
        "id",
        "member_id",
        "name",
        "date",
        "headphone_min",
        "sleep_time",
        "sleep_time_ampm",
        "sleep_onset_min",
        "wakeup_count",
        "fallback_sleep",
        "disturbance",
        "disturbance_scale",
        "morning_wake_time",
        "wake_time",
        "sleep_quality",
        "created_at",
    ]
)

SLEEP_LOG_COLUMN_NAMES = {
    "id": "紀錄 ID",
    "member_id": "會員編號",
    "name": "姓名",
    "date": "日誌日期",
    "headphone_min": "耳機（分）",
    "sleep_time": "躺床時間",
    "sleep_time_ampm": "AM/PM",
    "sleep_onset_min": "入睡耗時（分）",
    "wakeup_count": "夜醒次數",
    "fallback_sleep": "醒後睡回",
    "disturbance": "干擾因素",
    "disturbance_scale": "干擾程度",
    "morning_wake_time": "醒來時間",
    "wake_time": "離床時間",
    "sleep_quality": "睡眠品質",
    "created_at": "送出時間",
}


def is_valid_member(member_id: str) -> bool:
    return bool(MEMBER_PATTERN.fullmatch(member_id)) and member_id in MEMBERS


def render_public_styles() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {
            background: linear-gradient(180deg, #f4f3fb 0%, #ffffff 42%);
        }
        .sleep-hero {
            padding: 1.5rem 1.6rem;
            border-radius: 1.25rem;
            color: white;
            background: linear-gradient(135deg, #2a2460 0%, #534ab7 100%);
            box-shadow: 0 8px 28px rgba(63, 52, 137, .18);
            margin-bottom: 1.2rem;
        }
        .sleep-hero h1 { margin: 0 0 .35rem; font-size: 1.65rem; }
        .sleep-hero p { margin: 0; opacity: .82; }
        .member-badge {
            display: inline-block;
            margin-top: .8rem;
            padding: .25rem .7rem;
            border-radius: 999px;
            background: rgba(255,255,255,.18);
            font-weight: 700;
            letter-spacing: .04em;
        }
        div[data-testid="stForm"] {
            background: rgba(255,255,255,.94);
            border: 1px solid rgba(63,52,137,.12);
            border-radius: 1.1rem;
            padding: 1.2rem 1.25rem;
            box-shadow: 0 3px 18px rgba(63,52,137,.08);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def validate_form(row: dict) -> list[str]:
    errors: list[str] = []
    if not row["name"].strip():
        errors.append("請填寫姓名。")
    if row["headphone_min"] < 0 or row["headphone_min"] > 600:
        errors.append("耳機使用時間必須介於 0 到 600 分鐘。")
    if row["sleep_onset_min"] < 0 or row["sleep_onset_min"] > 300:
        errors.append("入睡時間必須介於 0 到 300 分鐘。")
    if row["wakeup_count"] < 0 or row["wakeup_count"] > 20:
        errors.append("夜間醒來次數必須介於 0 到 20 次。")
    disturbances = row["disturbance"].split(",")
    if not row["disturbance"]:
        errors.append("請選擇至少一項干擾因素；若沒有干擾，請選擇「無」。")
    if "無" in disturbances and len(disturbances) > 1:
        errors.append("干擾因素選擇「無」時，不能同時選擇其他項目。")
    if row["disturbance_scale"] is None:
        errors.append("請選擇干擾程度。")
    if row["sleep_quality"] is None:
        errors.append("請選擇昨晚的睡眠品質。")
    return errors


def submit_sleep_log(row: dict) -> None:
    if not dashboard.SUPABASE_URL or not dashboard.SUPABASE_KEY:
        raise RuntimeError("Supabase 尚未完成設定")
    client = dashboard.get_supabase(dashboard.SUPABASE_URL, dashboard.SUPABASE_KEY)
    client.table("sleep_logs").insert(
        row,
        returning="minimal",
    ).execute()


def render_sleep_diary(member_id: str) -> None:
    render_public_styles()
    st.markdown(
        f"""
        <section class="sleep-hero">
          <h1>🌙 深睡體驗營—睡眠日誌</h1>
          <p>請依昨晚的實際睡眠狀況填寫，所有欄位都是必填。</p>
          <span class="member-badge">{member_id}</span>
        </section>
        """,
        unsafe_allow_html=True,
    )

    today = datetime.now(dashboard.TW_TZ).date()
    with st.form("sleep_diary", clear_on_submit=True, border=False):
        st.subheader("基本資料")
        name = st.text_input("姓名", max_chars=80)
        log_date = st.date_input("填寫日期", value=today, max_value=today)

        st.subheader("耳機使用")
        headphone_min = st.number_input(
            "昨晚使用耳機多久？（分鐘）",
            min_value=0,
            max_value=600,
            value=0,
            step=5,
        )

        st.subheader("入睡狀況")
        c1, c2 = st.columns(2)
        with c1:
            sleep_time = st.time_input("昨晚躺上床的時間", value=time(23, 0), step=300)
        with c2:
            sleep_time_ampm = st.selectbox(
                "躺床時段",
                options=["PM", "AM"],
                format_func=lambda value: "下午 PM（12 點前）" if value == "PM" else "上午 AM（12 點後）",
            )
        sleep_onset_min = st.number_input(
            "花多久才入睡？（分鐘）",
            min_value=0,
            max_value=300,
            value=15,
            step=5,
        )

        st.subheader("睡眠中斷")
        wakeup_count = st.number_input(
            "昨晚夜間醒來次數",
            min_value=0,
            max_value=20,
            value=0,
            step=1,
        )
        fallback_sleep = st.radio(
            "醒來後是否能睡回去？",
            ["無（未曾醒來）", "可以馬上睡回去", "可以，但需要花點時間", "不能"],
        )

        st.subheader("干擾因素")
        disturbance_labels = {
            "人": "👥 人／人際",
            "事，如情緒": "💭 事／情緒",
            "物，如環境": "🏠 物／環境",
            "無": "✓ 無干擾",
        }
        disturbance = st.pills(
            "昨晚有哪些因素干擾睡眠？",
            options=list(disturbance_labels),
            selection_mode="multi",
            format_func=lambda value: disturbance_labels[value],
            width="stretch",
            wrap=True,
        )
        st.caption("可複選；若沒有干擾，請只點選「無干擾」。")
        disturbance_scale = st.pills(
            "干擾程度（點選一項）",
            options=list(range(6)),
            selection_mode="single",
            required=True,
            width="stretch",
            wrap=True,
        )
        st.caption("0 無干擾　·　1 極輕微　·　2 輕微　·　3 普通　·　4 嚴重　·　5 非常嚴重")

        st.subheader("起床狀況")
        c3, c4 = st.columns(2)
        with c3:
            morning_wake_time = st.time_input("今天幾點醒來？", value=time(7, 0), step=300)
        with c4:
            wake_time = st.time_input("今天離床的時間？", value=time(7, 15), step=300)

        st.subheader("整體睡眠品質")
        sleep_quality = st.pills(
            "昨晚睡眠品質（點選一項）",
            options=list(range(1, 6)),
            selection_mode="single",
            required=True,
            width="stretch",
            wrap=True,
        )
        st.caption("1 非常糟糕　·　2 不好　·　3 普通　·　4 良好　·　5 非常好")

        submitted = st.form_submit_button("📤 送出睡眠日誌", type="primary", use_container_width=True)

    if submitted:
        row = {
            "member_id": member_id,
            "name": name.strip(),
            "date": log_date.isoformat(),
            "headphone_min": int(headphone_min),
            "sleep_time": sleep_time.strftime("%H:%M"),
            "sleep_time_ampm": sleep_time_ampm,
            "sleep_onset_min": int(sleep_onset_min),
            "wakeup_count": int(wakeup_count),
            "fallback_sleep": fallback_sleep.replace("（未曾醒來）", ""),
            "disturbance": ",".join(disturbance),
            "disturbance_scale": int(disturbance_scale) if disturbance_scale is not None else None,
            "morning_wake_time": morning_wake_time.strftime("%H:%M"),
            "wake_time": wake_time.strftime("%H:%M"),
            "sleep_quality": int(sleep_quality) if sleep_quality is not None else None,
        }
        errors = validate_form(row)
        if errors:
            for error in errors:
                st.error(error)
        else:
            try:
                submit_sleep_log(row)
                st.success("日誌已送出，謝謝您的填寫。", icon="✅")
            except Exception:
                st.error("日誌送出失敗，請稍後再試；若持續發生，請通知研究人員。")

    st.caption("為保護隱私，公開填寫頁不會顯示、匯出或刪除任何受試者的歷史資料。")


def app_base_url() -> str:
    configured = dashboard.get_setting("PUBLIC_APP_URL")
    if configured:
        return configured.rstrip("/")
    try:
        return str(st.context.url).rstrip("/")
    except Exception:
        return "http://localhost:8501"


def make_qr_png(url: str) -> bytes:
    image = qrcode.make(url)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def render_member_link_tool() -> None:
    with st.sidebar.expander("🔗 會員填寫連結 / QR Code"):
        member_id = st.selectbox("會員編號", MEMBERS, key="qr_member")
        url = f"{app_base_url()}/?member={member_id}"
        qr_png = make_qr_png(url)
        st.image(qr_png, caption=member_id, use_container_width=True)
        st.code(url, language=None)
        st.link_button("開啟填寫頁", url, use_container_width=True)
        st.download_button(
            "下載 QR Code",
            data=qr_png,
            file_name=f"{member_id}.png",
            mime="image/png",
            use_container_width=True,
        )


def fetch_sleep_logs(member_id: str, start_date, end_date) -> list[dict]:
    """以伺服器端管理金鑰讀取研究人員查詢範圍內的日誌。"""
    admin_key = dashboard.get_setting("SUPABASE_ADMIN_KEY")
    if not dashboard.SUPABASE_URL or not admin_key:
        raise RuntimeError("SUPABASE_ADMIN_KEY 尚未完成設定")

    client = dashboard.get_supabase(dashboard.SUPABASE_URL, admin_key)
    query = (
        client.table("sleep_logs")
        .select(SLEEP_LOG_SELECT)
        .gte("date", start_date.isoformat())
        .lte("date", end_date.isoformat())
        .order("date", desc=True)
        .order("created_at", desc=True)
        .limit(500)
    )
    if member_id != "全部會員":
        query = query.eq("member_id", member_id)
    response = query.execute()
    return list(response.data or [])


def render_sleep_log_records() -> None:
    """只在研究人員登入後顯示 Supabase 睡眠日誌。"""
    st.header("📝 已送出的睡眠日誌")
    st.caption("資料來自 Supabase；此頁目前只提供查詢，不提供修改或刪除。")

    if not dashboard.get_setting("SUPABASE_ADMIN_KEY"):
        st.warning(
            "尚未設定 SUPABASE_ADMIN_KEY，因此無法安全讀取歷史日誌。"
            "請先在 Streamlit Secrets 加入 Supabase Secret Key。"
        )
        return

    today = datetime.now(dashboard.TW_TZ).date()
    filter_col1, filter_col2 = st.columns([1, 2])
    with filter_col1:
        member_id = st.selectbox(
            "會員編號",
            ["全部會員", *MEMBERS],
            key="sleep_log_member_filter",
        )
    with filter_col2:
        selected_dates = st.date_input(
            "日誌日期範圍",
            value=(today - timedelta(days=180), today),
            max_value=today,
            key="sleep_log_date_filter",
        )

    if isinstance(selected_dates, (tuple, list)):
        if len(selected_dates) == 2:
            start_date, end_date = selected_dates
        elif len(selected_dates) == 1:
            start_date = end_date = selected_dates[0]
        else:
            start_date = end_date = today
    else:
        start_date = end_date = selected_dates

    name_keyword = st.text_input(
        "姓名篩選（選填）",
        placeholder="輸入部分姓名",
        key="sleep_log_name_filter",
    ).strip()

    try:
        records = fetch_sleep_logs(member_id, start_date, end_date)
    except Exception as exc:
        print(f"Supabase sleep log query failed: {type(exc).__name__}")
        st.error("無法讀取睡眠日誌，請確認 SUPABASE_ADMIN_KEY、RLS 與資料表權限設定。")
        return

    frame = pd.DataFrame(records)
    if frame.empty:
        st.info("目前篩選條件下沒有睡眠日誌。")
        return

    if name_keyword:
        frame = frame[
            frame["name"].fillna("").astype(str).str.contains(name_keyword, case=False, regex=False)
        ]
    if frame.empty:
        st.info("沒有符合姓名條件的睡眠日誌。")
        return

    frame["created_at"] = (
        pd.to_datetime(frame["created_at"], utc=True, errors="coerce")
        .dt.tz_convert(dashboard.TW_TZ)
        .dt.strftime("%Y-%m-%d %H:%M:%S")
    )

    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("紀錄數", len(frame))
    metric_col2.metric("會員數", frame["member_id"].nunique())
    quality = pd.to_numeric(frame["sleep_quality"], errors="coerce").mean()
    metric_col3.metric("平均睡眠品質", f"{quality:.1f} / 5" if pd.notna(quality) else "N/A")

    display_frame = frame.rename(columns=SLEEP_LOG_COLUMN_NAMES)
    display_columns = [
        SLEEP_LOG_COLUMN_NAMES[column]
        for column in SLEEP_LOG_COLUMN_NAMES
        if SLEEP_LOG_COLUMN_NAMES[column] in display_frame.columns
    ]
    st.dataframe(
        display_frame[display_columns],
        width="stretch",
        hide_index=True,
        height=min(600, 38 + len(display_frame) * 35),
    )

    record_options = {
        f"#{row['id']}｜{row['member_id']}｜{row['date']}｜{row['name']}": row
        for row in frame.to_dict("records")
    }
    with st.expander("查看單筆完整內容"):
        selected_label = st.selectbox(
            "選擇紀錄",
            list(record_options),
            key="sleep_log_detail_record",
        )
        record = record_options[selected_label]
        detail_col1, detail_col2, detail_col3 = st.columns(3)
        detail_col1.metric("睡眠品質", f"{record['sleep_quality']} / 5")
        detail_col2.metric("入睡耗時", f"{record['sleep_onset_min']} 分")
        detail_col3.metric("夜醒次數", f"{record['wakeup_count']} 次")
        st.markdown(
            f"""
            - **會員／姓名：** `{record['member_id']}`／{record['name']}
            - **日誌日期：** {record['date']}
            - **躺床：** {record['sleep_time']} ({record['sleep_time_ampm']})
            - **醒來／離床：** {record['morning_wake_time']}／{record['wake_time']}
            - **耳機使用：** {record['headphone_min']} 分鐘
            - **醒後睡回：** {record['fallback_sleep']}
            - **干擾因素／程度：** {record['disturbance']}／{record['disturbance_scale']}
            - **送出時間：** {record['created_at']}
            """
        )


def render_researcher_portal() -> None:
    if st.session_state.get("token"):
        render_member_link_tool()
        monitor_tab, log_tab = st.tabs(["🏥 健康監控", "📝 睡眠日誌紀錄"])
        with monitor_tab:
            dashboard.main()
        with log_tab:
            render_sleep_log_records()
        return
    else:
        render_public_styles()
        st.markdown(
            """
            <section class="sleep-hero">
              <h1>🌙 深睡體驗營</h1>
              <p>受試者請掃描研究人員提供的個人 QR Code；研究人員請從左側登入。</p>
            </section>
            """,
            unsafe_allow_html=True,
        )
        st.info("公開首頁不會列出會員或健康資料。")
    dashboard.main()


def main() -> None:
    raw_member = str(st.query_params.get("member", "")).strip().lower()
    if raw_member:
        if not is_valid_member(raw_member):
            st.error("無效的會員連結，請重新掃描研究人員提供的 QR Code。")
            st.stop()
        render_sleep_diary(raw_member)
        return
    render_researcher_portal()


if __name__ == "__main__":
    main()
