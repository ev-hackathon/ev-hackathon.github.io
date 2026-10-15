"""
충북 도심형 EV 충전소 관제 & 동적 요금 플랫폼 (Streamlit)

실행:  streamlit run main.py
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import config
from config import PricingPolicy
from utils import api, demo_data, logic
from utils import ui_components as ui

st.set_page_config(page_title="충북 EV 동적 요금 & 관제 플랫폼", page_icon="⚡", layout="wide")
ui.inject_css()

SRC_API, SRC_SNAP, SRC_DEMO = "실시간 API", "저장된 스냅샷", "데모 (가상 데이터)"


# ─────────────────────────────────────────────────────────────
# 데이터 로딩 (실패한 호출은 캐시되지 않음)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=config.CACHE_TTL_SEC, show_spinner=False)
def load_api(region: str) -> tuple[list[dict], str]:
    rows = api.fetch_region(region)
    return rows, datetime.now(logic.KST).strftime("%Y-%m-%d %H:%M")


@st.cache_data(show_spinner=False)
def load_snapshot(region: str, _mtime: float) -> tuple[list[dict], str] | None:
    return api.load_snapshot(region)


@st.cache_data(show_spinner=False)
def load_demo(region: str, hour_key: str) -> list[dict]:
    return demo_data.generate(region)


def get_rows(region: str, source: str) -> tuple[list[dict], str, str]:
    """(rows, 실제 사용된 소스, 기준 시각). 실시간 실패 시 스냅샷 → 데모 순으로 자동 대체."""
    if source == SRC_API:
        try:
            rows, as_of = load_api(region)
            if rows:
                return rows, SRC_API, f"{as_of} 조회"
            st.warning("API 응답에 충전기 데이터가 없습니다. 저장된 데이터로 대체합니다.")
        except api.ApiError as e:
            st.warning(f"실시간 API 호출 실패 — {e}  \n저장된 데이터로 대체합니다.")
        source = SRC_SNAP
    if source == SRC_SNAP:
        path = api.snapshot_path(region)
        snap = load_snapshot(region, path.stat().st_mtime) if path.exists() else None
        if snap and snap[0]:
            return snap[0], SRC_SNAP, f"{snap[1].replace('T', ' ')[:16]} 저장"
        if source == SRC_SNAP:
            st.info(f"'{region}' 스냅샷이 없어 데모 데이터로 표시합니다. (scripts/save_snapshot.py로 저장 가능)")
    now = datetime.now(logic.KST)
    return load_demo(region, now.strftime("%Y%m%d%H")), SRC_DEMO, now.strftime("%Y-%m-%d %H:%M 생성")


# ─────────────────────────────────────────────────────────────
# 사이드바
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 지역")
    region = st.selectbox("충북 시·군", list(config.REGIONS), index=0, label_visibility="collapsed")

    has_key = api.get_api_key() is not None
    has_snap = api.snapshot_path(region).exists()
    default_src = 0 if has_key else (1 if has_snap else 2)
    source = st.radio("데이터 소스", [SRC_API, SRC_SNAP, SRC_DEMO], index=default_src,
                      help="발표장 네트워크가 불안정하면 '저장된 스냅샷'을 쓰세요. 데모는 가상 데이터입니다.")
    if not has_key:
        st.caption("`.env`에 EV_API_KEY가 없어 실시간 조회를 할 수 없습니다.")

    st.markdown("### 요금 정책")
    d = config.DEFAULT_POLICY
    threshold = st.slider("할인 기준: 대기 충전기 비율 ≥", 0.1, 0.9, d.discount_threshold, 0.05,
                          help="사용 가능한 충전기(대기+충전중) 중 대기 비율이 이 값 이상이면 '여유'로 보고 할인합니다.")
    discount = st.slider("여유 거점 할인율 (%)", 0, 50, int(d.discount_rate * 100), 5)
    surcharge = st.slider("만차 거점 할증율 (%)", 0, 50, int(d.surcharge_rate * 100), 5)
    use_tod = st.toggle("시간대 가중치 적용", value=d.use_time_of_day,
                        help=" / ".join(f"{b.start_hour}~{b.end_hour}시 ×{b.multiplier} ({b.label})" for b in d.time_bands)
                        + " — 예시 가정값, config.py에서 수정")
    radius = st.slider("대체 거점 탐색 반경 (km)", 0.5, 10.0, config.REDIRECT_RADIUS_KM, 0.5)

    st.markdown("### 표시")
    exclude_limited = st.checkbox("이용자 제한 충전소 제외 (예: 아파트 전용)", value=config.EXCLUDE_LIMITED_DEFAULT)
    show_redirect = st.checkbox("지도에 대체 거점 안내선 표시", value=True)
    if st.button("실시간 데이터 새로 받기", width="stretch"):
        load_api.clear()

policy = PricingPolicy(discount_threshold=threshold, discount_rate=discount / 100, surcharge_rate=surcharge / 100,
                       use_time_of_day=use_tod, time_bands=d.time_bands)

# ─────────────────────────────────────────────────────────────
# 계산
# ─────────────────────────────────────────────────────────────
with st.spinner(f"{region} 충전기 데이터를 불러오는 중..."):
    rows, used_source, as_of = get_rows(region, source)

now = datetime.now(logic.KST)
chargers = logic.prepare_chargers(rows, exclude_limited=exclude_limited)
stations = logic.summarize_stations(chargers, policy, now)
stations = logic.recommend_alternatives(stations, radius)
kpis = logic.region_kpis(stations)

is_demo = used_source == SRC_DEMO
ui.header(region, used_source, as_of, is_demo)
if is_demo:
    st.caption("가상 데이터 모드입니다. 충전소 이름, 위치, 상태는 시연용으로 만든 값입니다.")

if stations.empty:
    st.error("표시할 충전소가 없습니다. 지역 또는 필터를 확인하세요.")
    st.stop()

ui.kpi_cards(kpis, radius)

tab_map, tab_list, tab_stats, tab_algo = st.tabs(["지도", "거점 목록", "지역 분석", "요금 계산 방식"])

with tab_map:
    col_map, col_side = st.columns([7, 3], gap="medium")
    with col_map:
        ui.legend()
        st_folium(ui.build_map(stations, region, show_redirect=show_redirect),
                  height=600, use_container_width=True, returned_objects=[], key=f"map-{region}")
    with col_side:
        st.markdown("##### 혼잡 거점에서 옮겨 갈 곳")
        st.caption(f"충전 중인 차량이 많은 순, 반경 {radius:g}km 안의 할인 거점")
        ui.redirect_list(stations)
        st.markdown("##### 거점 상태 분포")
        st.altair_chart(ui.status_chart(stations), width="stretch")

with tab_list:
    view = stations.copy()
    view["상태"] = view["status"].map(lambda s: config.STATUS_META[s]["label"])
    view["대체 거점"] = view.apply(lambda r: f"{r['alt_name']} ({r['alt_speed']} · {r['alt_dist_km']:.1f}km)" if r["alt_name"] else "", axis=1)
    cols = {
        "statNm": "충전소명", "상태": "상태", "avail_ratio": "대기율", "fast_avail": "급속 대기", "fast_total": "급속 전체",
        "fast_price": "급속 단가", "slow_avail": "완속 대기", "slow_total": "완속 전체", "slow_price": "완속 단가",
        "대체 거점": "대체 거점", "facility": "시설", "parking": "주차", "busiNm": "운영기관", "addr": "주소",
    }
    status_filter = st.multiselect("상태 필터", [m["label"] for m in config.STATUS_META.values()], default=[],
                                   placeholder="전체 상태")
    if status_filter:
        view = view[view["상태"].isin(status_filter)]
    table = view[list(cols)].rename(columns=cols).sort_values(["대기율"], na_position="last")
    st.dataframe(table, hide_index=True, height=560, placeholder="-", column_config={
        "대기율": st.column_config.ProgressColumn("대기율", format="percent", min_value=0, max_value=1),
        "급속 단가": st.column_config.NumberColumn("급속 단가(원/kWh)", format="%.1f"),
        "완속 단가": st.column_config.NumberColumn("완속 단가(원/kWh)", format="%.1f"),
    })
    st.download_button("CSV로 받기", table.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"ev_{config.REGIONS[region]['code']}_{now:%Y%m%d_%H%M}.csv", mime="text/csv")

with tab_stats:
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("##### 급속 · 완속 충전기 상태")
        st.altair_chart(ui.speed_chart(chargers), width="stretch")
        st.markdown("##### 시설 유형별 충전소")
        fac = stations.groupby("facility").agg(충전소=("statId", "size"), 충전기=("total", "sum"),
                                              대기율=("avail_ratio", "mean")).sort_values("충전소", ascending=False)
        fac.index.name = "시설 유형"
        st.dataframe(fac, column_config={"대기율": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
    with c2:
        st.markdown("##### 상위 운영기관 (충전기 수)")
        st.altair_chart(ui.operator_chart(stations), width="stretch")
        st.markdown("##### 거점 상태 분포")
        st.altair_chart(ui.status_chart(stations), width="stretch")

with tab_algo:
    tod, tod_label = logic.time_multiplier(now.hour, policy)
    a1, a2 = st.columns([5, 4], gap="large")
    with a1:
        st.markdown("##### 현재 정책으로 계산한 단가 (원/kWh)")
        st.caption(f"사이드바 슬라이더를 움직이면 즉시 반영됩니다 · 현재 시각 {now:%H}시 · {tod_label} (×{tod:g})")
        st.altair_chart(ui.policy_chart(policy, tod), width="stretch")
    with a2:
        st.markdown("##### 판정 규칙")
        st.markdown(f"""
1. 충전기 상태코드로 **대기(2) · 충전중(3) · 고장(1·4·5)** 을 셉니다.
2. **대기율 = 대기 ÷ (대기 + 충전중)** — 고장 충전기는 분모에서 뺍니다.
3. 급속·완속을 **따로** 판정합니다.

| 상태 | 조건 | 요금 |
|---|---|---|
| 여유 | 대기율 ≥ {threshold:.0%} | 기본 × {1 - discount / 100:.2f} |
| 보통 | 대기 1대 이상 | 기본 × 1.00 |
| 혼잡 | 대기 0대 (전부 충전 중) | 기본 × {1 + surcharge / 100:.2f} |
| 불가 | 고장·점검·운영중지 | 요금 미산정 |

4. 혼잡·불가 거점에서 반경 **{radius:g}km** 안의 가장 가까운 **같은 속도(급속/완속) 여유 거점**을 대체 거점으로 안내합니다.
""")
        st.markdown("##### 출력 구간별 기본 단가")
        tiers, lower = [], 0
        for upper, rate in config.BASE_RATE_TIERS:
            tiers.append({"출력 구간": f"{lower:g}kW 이상" + ("" if upper == float("inf") else f" ~ {upper:g}kW 미만"),
                          "기본 단가(원/kWh)": rate})
            lower = upper
        st.dataframe(pd.DataFrame(tiers), hide_index=True)
        st.caption("2026.4 공공 충전요금 5단계 개편 행정예고(언론 보도) 기준 · 최종 고시로 확인 후 config.py에서 수정")

st.divider()
st.caption("데이터: 한국환경공단 전기자동차 충전소 정보(공공데이터포털) · 지도: © OpenStreetMap contributors · "
           "행정경계: 통계청 2018(southkorea-maps) · 요금은 정책 시뮬레이션 값이며 실제 청구 요금이 아닙니다.")
