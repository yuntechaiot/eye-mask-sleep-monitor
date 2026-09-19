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
from datetime import datetime, time

import qrcode
import streamlit as st


dashboard = importlib.import_module("數據監控室")

MEMBER_PATTERN = re.compile(r"^ycc\d{8}$")
MEMBERS = [f"ycc{i:08d}" for i in range(1, 51)]


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
    if "無" in disturbances and len(disturbances) > 1:
        errors.append("干擾因素選擇「無」時，不能同時選擇其他項目。")
    return errors


def submit_sleep_log(row: dict) -> None:
    if not dashboard.SUPABASE_URL or not dashboard.SUPABASE_KEY:
        raise RuntimeError("Supabase 尚未完成設定")
    client = dashboard.get_supabase(dashboard.SUPABASE_URL, dashboard.SUPABASE_KEY)
    client.table("sleep_logs").insert(row).execute()


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
        disturbance = st.multiselect(
            "昨晚是否有人事物干擾您的睡眠？（可複選）",
            ["人", "事，如情緒", "物，如環境", "無"],
            default=["無"],
        )
        disturbance_scale = st.select_slider(
            "干擾程度",
            options=list(range(0, 6)),
            value=0,
            help="0 代表無干擾，5 代表非常嚴重。",
        )

        st.subheader("起床狀況")
        c3, c4 = st.columns(2)
        with c3:
            morning_wake_time = st.time_input("今天幾點醒來？", value=time(7, 0), step=300)
        with c4:
            wake_time = st.time_input("今天離床的時間？", value=time(7, 15), step=300)

        st.subheader("整體睡眠品質")
        sleep_quality = st.select_slider(
            "昨晚睡眠品質",
            options=list(range(1, 6)),
            value=3,
            format_func=lambda value: {
                1: "1—非常糟糕",
                2: "2—不好",
                3: "3—普通",
                4: "4—良好",
                5: "5—非常好",
            }[value],
        )

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
            "disturbance_scale": int(disturbance_scale),
            "morning_wake_time": morning_wake_time.strftime("%H:%M"),
            "wake_time": wake_time.strftime("%H:%M"),
            "sleep_quality": int(sleep_quality),
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


def render_researcher_portal() -> None:
    if st.session_state.get("token"):
        render_member_link_tool()
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
