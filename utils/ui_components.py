"""화면 구성 요소: 스타일, 헤더, KPI 카드, 지도, 차트."""
from __future__ import annotations

import json
from functools import lru_cache
from html import escape

import altair as alt
import folium
import numpy as np
import pandas as pd
import streamlit as st
from branca.element import Element

import config

STATUS_ORDER = list(config.STATUS_META)
STATUS_COLORS = [config.STATUS_META[s]["color"] for s in STATUS_ORDER]


# ─────────────────────────────────────────────────────────────
# 스타일 · 헤더 · 요약 수치  (웹사이트 web/index.html과 같은 색·글꼴 체계)
# ─────────────────────────────────────────────────────────────
INK, INK2, INK3, RULE, SURFACE = "#17201c", "#4a5550", "#7d8782", "#e2e6e4", "#ffffff"
FREE, BUSY = config.STATUS_META["여유"]["color"], config.STATUS_META["혼잡"]["color"]

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input {{ font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; }}
.block-container {{ padding-top: 2.6rem; padding-bottom: 2rem; max-width: 1500px; }}
.ev-hero {{ display:flex; justify-content:space-between; align-items:flex-end; gap:12px 24px; flex-wrap:wrap; padding: 0 0 14px; margin-bottom: 16px; border-bottom: 1px solid {RULE}; }}
.ev-title {{ font-weight: 800; letter-spacing: -.02em; font-size: 32px; line-height: 1.1; color: {INK}; margin: 0 0 6px; }}
.ev-sub {{ font-size: 15px; color: {INK2}; }}
.ev-meta {{ display:grid; gap:2px; text-align:right; font-size: 13.5px; color: {INK2}; }}
.ev-meta b {{ color: {INK}; font-weight: 600; }}
.ev-flag {{ display:inline-block; font-size:12px; color:{BUSY}; border:1px solid currentColor; border-radius:2px; padding:0 5px; margin-left:6px; }}
.ev-figs {{ display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap:16px 28px; margin: 4px 0 18px; }}
@media (max-width: 1100px) {{ .ev-figs {{ grid-template-columns: repeat(2, minmax(0,1fr)); }} }}
.ev-fig .val {{ font-weight: 800; letter-spacing: -.02em; font-size: 32px; line-height: 1.05; color:{INK}; font-variant-numeric: tabular-nums; }}
.ev-fig .val small {{ font-weight: 500; font-size: 14px; color:{INK2}; margin-left:3px; }}
.ev-fig .lbl {{ font-size: 14px; font-weight: 600; color:{INK}; margin-top: 4px; display:flex; align-items:center; gap:6px; }}
.ev-fig .sub {{ font-size: 12.5px; color:{INK3}; margin-top: 1px; }}
.ev-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; flex:none; }}
.ev-alt {{ padding: 8px 0; border-bottom: 1px solid {RULE}; font-size: 14px; }}
.ev-alt .route {{ display:flex; align-items:center; gap:6px; color:{INK}; min-width:0; }}
.ev-alt .route.to {{ padding-left: 2px; margin-top: 2px; }}
.ev-alt .meta {{ color:{INK3}; font-size: 12.5px; margin-top: 2px; padding-left: 36px; }}
.ev-legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:13.5px; color:{INK2}; margin: 2px 0 8px; align-items:center; }}
.ev-legend span {{ display:inline-flex; align-items:center; gap:6px; }}
.ev-ring {{ background: transparent !important; box-shadow: inset 0 0 0 2px {config.STATUS_META["불가"]["color"]}; }}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def header(region: str, source_label: str, as_of: str, is_demo: bool) -> None:
    flag = '<span class="ev-flag">가상 데이터</span>' if is_demo else ""
    st.markdown(f"""
<div class="ev-hero">
  <div>
    <div class="ev-title">충북 EV 요금 관제</div>
    <div class="ev-sub">충전기 대기율로 혼잡도를 판정하고, 여유 거점은 할인해 피크 수요를 인근으로 분산합니다.</div>
  </div>
  <div class="ev-meta">
    <span><b>충청북도 {escape(region)}</b>{flag}</span>
    <span>{escape(source_label)}, {escape(as_of)}</span>
  </div>
</div>""", unsafe_allow_html=True)


def _dot(status: str) -> str:
    ring = " ev-ring" if status in ("불가", "미확인") else ""
    return f'<span class="ev-dot{ring}" style="background:{config.STATUS_META[status]["color"]}"></span>'


def _fig(label: str, value: str, sub: str = "", unit: str = "", status: str | None = None) -> str:
    dot = _dot(status) if status else ""
    return (f'<div class="ev-fig"><div class="val">{value}<small>{unit}</small></div>'
            f'<div class="lbl">{dot}{label}</div><div class="sub">{sub}</div></div>')


def kpi_cards(k: dict, radius_km: float) -> None:
    sc = k["status_counts"]
    idle = "-" if pd.isna(k["idle_rate"]) else f"{k['idle_rate'] * 100:.0f}"
    need = k["needs_redirect"]
    html = '<div class="ev-figs">' + "".join([
        _fig("충전소", f"{k['stations']:,}", f"충전기 {k['chargers']:,}대, 급속 {k['fast_chargers']:,}대", "곳"),
        _fig("현재 대기율", idle, f"사용 가능 {k['available'] + k['charging']:,}대 중 {k['available']:,}대 대기", "%"),
        _fig("여유 거점", f"{sc['여유']:,}", "할인 적용", "곳", "여유"),
        _fig("혼잡 거점", f"{sc['혼잡']:,}", f"할증 적용, 이용 불가 {sc['불가']:,}곳 별도", "곳", "혼잡"),
        _fig("대체 거점 안내 가능", f"{k['redirectable']:,}", f"혼잡·불가 {need:,}곳 중, 반경 {radius_km:g}km", "곳"),
    ]) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


def legend() -> None:
    items = "".join(f'<span>{_dot(s)}{m["label"]}</span>' for s, m in config.STATUS_META.items())
    items += f'<span><svg width="22" height="8"><path d="M1 4h15" stroke="{FREE}" stroke-width="2" stroke-dasharray="4 3"/><path d="M15 1l5 3-5 3z" fill="{FREE}"/></svg>대체 거점 안내</span>'
    st.markdown(f'<div class="ev-legend">{items}</div>', unsafe_allow_html=True)


def redirect_list(stations: pd.DataFrame, limit: int = 6) -> None:
    df = stations[(stations["alt_statId"].fillna("") != "") & (stations["status"] == "혼잡")]
    df = df.sort_values(["charging", "alt_dist_km"], ascending=[False, True]).head(limit)
    if df.empty:
        st.caption("지금은 반경 안에서 안내할 혼잡 거점이 없습니다.")
        return
    arrow = f'<svg width="14" height="12" viewBox="0 0 14 12"><path d="M2 0v6h10M8 2l4 4-4 4" fill="none" stroke="{INK3}" stroke-width="1.4"/></svg>'
    for _, r in df.iterrows():
        # 같은 출력 구간(기본 단가가 같을 때)만 절약액을 표시. 출력이 다르면 단가 차이가 혼잡 때문만은 아니다.
        src_base = r["fast_base"] if r["fast_total"] > 0 else r["slow_base"]
        same = pd.notna(src_base) and pd.notna(r["alt_base"]) and abs(src_base - r["alt_base"]) < 0.01
        save = r["price"] - r["alt_price"] if same and pd.notna(r["price"]) and pd.notna(r["alt_price"]) else np.nan
        save_txt = f", 같은 출력 대비 {save:,.0f}원 저렴" if pd.notna(save) and save > 0 else ""
        st.markdown(f"""<div class="ev-alt"><div class="route">{_dot("혼잡")}{escape(r['statNm'])}</div>
<div class="route to">{arrow}{_dot("여유")}{escape(r['alt_name'])}</div>
<div class="meta">{r['alt_dist_km']:.1f}km 거리, {r['alt_speed']} {_fmt_price(r['alt_price'])}{save_txt}</div></div>""", unsafe_allow_html=True)


def _fmt_price(v) -> str:
    return "-" if pd.isna(v) else f"{v:,.1f}원"


# ─────────────────────────────────────────────────────────────
# 지도
# ─────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _boundaries() -> dict:
    try:
        return json.loads(config.BOUNDARY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"features": []}


def _popup_html(r: pd.Series) -> str:
    meta = config.STATUS_META.get(r["status"], config.STATUS_META["미확인"])

    def line(icon, label, avail, total, price, status):
        if total == 0:
            return ""
        color = FREE if avail > 0 else BUSY
        return (f'<div><b>{label}</b> 대기 <b style="color:{color}">{avail}</b>/{total}대'
                f' · <b>{_fmt_price(price)}</b><span style="color:#64748b">/kWh</span></div>')

    alt_html = ""
    if r.get("alt_name"):
        alt_html = (f'<div style="margin-top:6px;padding:6px 8px;background:#dce9f0;border-radius:3px;color:#18221e">'
                    f'➜ 대체 거점: <b>{escape(r["alt_name"])}</b> ({r["alt_dist_km"]:.1f}km, {r["alt_speed"]} {_fmt_price(r["alt_price"])})</div>')
    return f"""
<div style="font-family:'Noto Sans KR',sans-serif;width:250px;font-size:12.5px;line-height:1.65;color:#18221e">
  <div style="font-size:14px;font-weight:800">{escape(r['statNm'])}</div>
  <div style="color:#4c5953">{escape(r['facility'])}, 주차 {escape(r['parking'])}, {escape(r['busiNm'])}</div>
  <div style="margin:6px 0"><span style="background:{meta['color']};color:#fff;border-radius:2px;padding:1px 7px;font-weight:600">{meta['label']}</span></div>
  {line('', '급속', r['fast_avail'], r['fast_total'], r['fast_price'], r['fast_status'])}
  {line('', '완속', r['slow_avail'], r['slow_total'], r['slow_price'], r['slow_status'])}
  {alt_html}
  <div style="color:#94a3b8;font-size:11.5px;margin-top:6px">{escape(r['addr'])}</div>
</div>"""


def build_map(stations: pd.DataFrame, region_name: str, *, show_redirect: bool = True) -> folium.Map:
    code = config.REGIONS[region_name]["code"]
    center = config.REGIONS[region_name]["center"]
    m = folium.Map(location=center, zoom_start=12, tiles="OpenStreetMap", prefer_canvas=True, control_scale=True)

    for f in _boundaries()["features"]:
        if f["properties"]["zscode"] == code:
            folium.GeoJson(f, name="행정구역", style_function=lambda _: {
                "color": INK, "weight": 1.6, "fillColor": SURFACE, "fillOpacity": 0.35,
            }).add_to(m)

    if stations.empty:
        return m

    if show_redirect:
        links = stations[stations["alt_statId"].fillna("") != ""]
        for _, r in links.iterrows():
            folium.PolyLine([(r["lat"], r["lng"]), (r["alt_lat"], r["alt_lng"])], color=FREE,
                            weight=3, opacity=0.85, dash_array="6 4").add_to(m)

    order = {"불가": 0, "미확인": 0, "보통": 1, "여유": 2, "혼잡": 3}  # 혼잡을 맨 위에 그림
    for _, r in stations.sort_values("status", key=lambda s: s.map(order)).iterrows():
        color = config.STATUS_META.get(r["status"], config.STATUS_META["미확인"])["color"]
        price = _fmt_price(r["price"])
        folium.CircleMarker(
            location=(r["lat"], r["lng"]),
            radius=float(min(5 + 1.6 * np.sqrt(r["total"]), 13)),
            color="#ffffff", weight=1.5, fill=True, fill_color=color, fill_opacity=0.92,
            popup=folium.Popup(_popup_html(r), max_width=280),
            tooltip=f"{r['statNm']}, {config.STATUS_META.get(r['status'], {}).get('label', '')}, {price}",
        ).add_to(m)

    pad = 0.004
    m.fit_bounds([[stations["lat"].min() - pad, stations["lng"].min() - pad],
                  [stations["lat"].max() + pad, stations["lng"].max() + pad]])
    m.get_root().html.add_child(Element(
        "<style>.leaflet-container{font-family:'Noto Sans KR',sans-serif}"
        ".leaflet-tile-pane{filter:saturate(.45) contrast(.95) brightness(1.02)}</style>"))
    return m


# ─────────────────────────────────────────────────────────────
# 차트 (Altair)
# ─────────────────────────────────────────────────────────────
_AXIS = dict(labelFontSize=12, titleFontSize=12, labelColor="#334155", titleColor="#64748b")


def status_chart(stations: pd.DataFrame) -> alt.Chart:
    counts = stations["status"].value_counts().reindex(STATUS_ORDER, fill_value=0).reset_index()
    counts.columns = ["상태", "거점 수"]
    counts["표시"] = counts["상태"].map(lambda s: config.STATUS_META[s]["label"])
    base = alt.Chart(counts).encode(
        y=alt.Y("표시:N", sort=[config.STATUS_META[s]["label"] for s in STATUS_ORDER], title=None, axis=alt.Axis(**_AXIS)),
        x=alt.X("거점 수:Q", title="충전소 수", axis=alt.Axis(**_AXIS, tickMinStep=1)),
    )
    bars = base.mark_bar(cornerRadiusEnd=2, height=22).encode(
        color=alt.Color("상태:N", scale=alt.Scale(domain=STATUS_ORDER, range=STATUS_COLORS), legend=None),
        tooltip=["표시", "거점 수"])
    text = base.mark_text(align="left", dx=4, fontSize=12, color="#334155").encode(text="거점 수:Q")
    return (bars + text).properties(height=190)


def speed_chart(chargers: pd.DataFrame) -> alt.Chart:
    df = chargers[chargers["speed"].isin(["급속", "완속"])].copy()
    order = ["대기", "충전중", "고장·점검", "미확인"]
    df["상태"] = np.select([df["is_avail"], df["is_charging"], df["is_fault"]], order[:3], order[3])
    agg = df.groupby(["speed", "상태"]).size().reset_index(name="대수")
    agg["순서"] = agg["상태"].map(order.index)
    agg["비율"] = agg["대수"] / agg.groupby("speed")["대수"].transform("sum")
    return alt.Chart(agg).mark_bar().encode(
        y=alt.Y("speed:N", title=None, sort=["급속", "완속"], axis=alt.Axis(**_AXIS),
                scale=alt.Scale(paddingInner=0.35)),
        x=alt.X("대수:Q", stack="normalize", title="충전기 상태 비율", axis=alt.Axis(**_AXIS, format="%")),
        color=alt.Color("상태:N", sort=order, scale=alt.Scale(domain=order, range=[FREE, BUSY, config.STATUS_META["불가"]["color"], "#c9d0cc"]),
                        legend=alt.Legend(orient="top", title=None, labelFontSize=12)),
        order=alt.Order("순서:Q"),
        tooltip=["speed", "상태", "대수", alt.Tooltip("비율:Q", format=".0%")],
    ).properties(height=210)


def operator_chart(stations: pd.DataFrame, top: int = 6) -> alt.Chart:
    df = stations.groupby("busiNm")["total"].sum().nlargest(top).reset_index()
    df.columns = ["운영기관", "충전기 수"]
    return alt.Chart(df).mark_bar(cornerRadiusEnd=2, color=INK2, height=18).encode(
        y=alt.Y("운영기관:N", sort="-x", title=None, axis=alt.Axis(**_AXIS, labelLimit=160)),
        x=alt.X("충전기 수:Q", title="충전기 수", axis=alt.Axis(**_AXIS)),
        tooltip=["운영기관", "충전기 수"],
    ).properties(height=36 * len(df) + 20)


def policy_chart(policy, tod: float = 1.0) -> alt.Chart:
    """현재 정책 변수로 계산한 출력 구간별 · 혼잡 상태별 단가 (슬라이더를 움직이면 즉시 바뀜)."""
    from utils.logic import base_rate_for_output, congestion_multiplier
    tiers = [("완속 7kW", 7), ("급속 50kW", 50), ("급속 100kW", 100), ("초급속 200kW", 200)]
    order = ["여유", "보통", "혼잡"]
    rows = [{"충전기": name, "상태": s, "단가": round(base_rate_for_output(kw) * congestion_multiplier(s, policy) * tod, 1)}
            for name, kw in tiers for s in order]
    df = pd.DataFrame(rows)
    base = alt.Chart(df).encode(
        x=alt.X("충전기:N", sort=[t[0] for t in tiers], title=None, axis=alt.Axis(**_AXIS, labelAngle=0)),
        xOffset=alt.XOffset("상태:N", sort=order),
        y=alt.Y("단가:Q", title="원/kWh", axis=alt.Axis(**_AXIS)),
    )
    bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        color=alt.Color("상태:N", sort=order, scale=alt.Scale(domain=order, range=[config.STATUS_META[s]["color"] for s in order]),
                        legend=alt.Legend(orient="bottom", title=None, labelFontSize=12)),
        tooltip=["충전기", "상태", "단가"])
    text = base.mark_text(dy=-7, fontSize=11, color="#334155").encode(text=alt.Text("단가:Q", format=".0f"))
    return (bars + text).properties(height=280)
