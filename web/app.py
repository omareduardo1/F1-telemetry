import streamlit as st
import fastf1
import pandas as pd
import numpy as np
import os
from pathlib import Path
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="🏎️ F1 Telemetry — Compare", layout="wide")

# crea una cache accanto a web/, a prescindere dalla working dir
CACHE_DIR = Path(__file__).resolve().parent / ".." / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

import fastf1
fastf1.Cache.enable_cache(str(CACHE_DIR))

# -------------------- Utils --------------------
def fmt_laptime(x) -> str:
    """Format Timedelta or seconds to MM:SS.mmm"""
    if x is None:
        return "-"
    if isinstance(x, pd.Timedelta):
        total_ms = int(x.total_seconds() * 1000)
    else:
        total_ms = int(float(x) * 1000)
    m, ms_rem = divmod(total_ms, 60_000)
    s, ms = divmod(ms_rem, 1000)
    return f"{m:01d}:{s:02d}.{ms:03d}"

@st.cache_data(show_spinner=False)
def get_schedule(year: int) -> pd.DataFrame:
    return fastf1.get_event_schedule(year, include_testing=False).copy()

@st.cache_data(show_spinner=True)
def load_session(year: int, rnd: int, kind: str):
    sess = fastf1.get_session(year, rnd, kind)  # kind: "R" (race) o "Q" (qualifying)
    sess.load(laps=True, telemetry=True, weather=False)
    return sess

def pick_lap(sess, driver: str, which: str = "best"):
    laps = sess.laps.pick_drivers([driver]).pick_quicklaps()
    if laps.empty:
        return None
    if which == "best":
        return laps.pick_fastest()
    try:
        ln = int(which)
        sel = laps[laps["LapNumber"] == ln]
        return sel.iloc[0] if not sel.empty else None
    except Exception:
        return laps.pick_fastest()

def lap_telemetry(lap):
    """
    Telemetry for a SINGLE lap with Distance calculated from Speed ​​(always 0..lap_length).
    We avoid the cumulative session Distance.
    """
    tel = lap.get_telemetry().copy()              # Time, Speed, nGear, Throttle, Brake, ...
    tel = tel.dropna(subset=["Time"]).sort_values("Time")

    # Relative time from the start of the lap
    t0 = lap["LapStartTime"]
    t_rel = (pd.to_timedelta(tel["Time"]) - pd.to_timedelta(t0)).dt.total_seconds().to_numpy()

    # Speed ​​in m/s (NaN handling)
    v_ms = pd.to_numeric(tel.get("Speed", 0), errors="coerce").fillna(0).to_numpy() / 3.6

    # Simple integration (trapezes/robust left-step)
    dt = np.diff(t_rel, prepend=t_rel[0])
    dt[0] = 0.0
    dist = np.cumsum(v_ms * dt)

    # Normalize and assign
    dist = dist - float(dist[0])
    tel["Distance"] = dist

    # (sanity) if for any reason > 12 km, scale to [0, max estimated]
    dmax = float(tel["Distance"].iloc[-1])
    if dmax > 12000:
        scale = max(dmax, 1.0)
        tel["Distance"] = tel["Distance"] * (7000.0 / scale)  # ~ long Spa-type circuit

    return tel

def overlay_telemetry_fig(tel_a, tel_b, name_a, name_b, title):
    # ensure columns exist (avoid crashes with incomplete data)
    for col, fill in [("Throttle", 0.0), ("Brake", 0.0), ("nGear", np.nan)]:
        if col not in tel_a.columns:
            tel_a[col] = fill
        if tel_b is not None and col not in tel_b.columns:
            tel_b[col] = fill

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        subplot_titles=("Speed", "Throttle / Brake", "Gear")
    )

    # --- SPEED ---
    fig.add_trace(
        go.Scatter(x=tel_a["Distance"], y=tel_a["Speed"],
                   name=f"{name_a} Speed", line=dict(width=2)),
        row=1, col=1
    )
    if tel_b is not None:
        fig.add_trace(
            go.Scatter(x=tel_b["Distance"], y=tel_b["Speed"],
                       name=f"{name_b} Speed", line=dict(width=2, dash="dot")),
            row=1, col=1
        )

    # --- THROTTLE & BRAKE ---
    fig.add_trace(
        go.Scatter(x=tel_a["Distance"], y=tel_a["Throttle"] * 100,
                   name=f"{name_a} Throttle", line=dict(width=2)),
        row=2, col=1
    )
    fig.add_trace(
        go.Scatter(x=tel_a["Distance"], y=tel_a["Brake"] * 100,
                   name=f"{name_a} Brake", line=dict(width=2)),
        row=2, col=1
    )
    if tel_b is not None:
        fig.add_trace(
            go.Scatter(x=tel_b["Distance"], y=tel_b["Throttle"] * 100,
                       name=f"{name_b} Throttle", line=dict(width=2, dash="dot")),
            row=2, col=1
        )
        fig.add_trace(
            go.Scatter(x=tel_b["Distance"], y=tel_b["Brake"] * 100,
                       name=f"{name_b} Brake", line=dict(width=2, dash="dot")),
            row=2, col=1
        )

    # --- GEAR ---
    fig.add_trace(
        go.Scatter(x=tel_a["Distance"], y=tel_a["nGear"],
                   name=f"{name_a} Gear", line=dict(width=2)),
        row=3, col=1
    )
    if tel_b is not None:
        fig.add_trace(
            go.Scatter(x=tel_b["Distance"], y=tel_b["nGear"],
                       name=f"{name_b} Gear", line=dict(width=2, dash="dot")),
            row=3, col=1
        )

    # --- Range e assi ---
    max_d = float(tel_a["Distance"].max())
    if tel_b is not None:
        max_d = max(max_d, float(tel_b["Distance"].max()))

    for r in [1, 2, 3]:
        fig.update_xaxes(range=[0, max_d], tickformat="~s", title_text="Distance (m)" if r == 3 else None, row=r, col=1)

    fig.update_yaxes(title_text="Speed (km/h)", row=1, col=1)
    fig.update_yaxes(title_text="%", row=2, col=1)
    fig.update_yaxes(title_text="Gear", row=3, col=1, dtick=1)

    # --- Layout ---
    fig.update_layout(
        title=title,
        height=900,
        margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", y=-0.08),
        template="plotly_dark"
    )

    return fig

def delta_time_fig(tel_a, tel_b, name_a, name_b):
    # Common distance limits
    dmin = max(tel_a["Distance"].min(), tel_b["Distance"].min())
    dmax = min(tel_a["Distance"].max(), tel_b["Distance"].max())
    grid = np.linspace(dmin, dmax, 1500)

    # "Proxy" calculation of time from speed
    def time_proxy(tel):
        v_ms = (tel["Speed"].clip(lower=1.0) / 3.6).to_numpy()
        dist = tel["Distance"].to_numpy()
        dt = np.r_[0, np.diff(dist) / v_ms[1:]]
        return np.cumsum(dt)

    ta = np.interp(grid, tel_a["Distance"], time_proxy(tel_a))
    tb = np.interp(grid, tel_b["Distance"], time_proxy(tel_b))
    delta = tb - ta  # >0 => A is in front

    # Build the grafic
    fig = go.Figure()

    # Dynamic colors: green if A forward, red if B
    colors = np.where(delta < 0, "limegreen", "orangered")

    fig.add_trace(go.Scatter(
        x=grid, y=delta,
        mode="lines",
        line=dict(width=3, color="white"),
        name=f"ΔT ({name_a} vs {name_b})",
        hovertemplate="Distanza: %{x:.0f} m<br>Δ tempo: %{y:.3f} s"
    ))

    # Fill under the curve (gap area)
    fig.add_trace(go.Scatter(
        x=np.concatenate([grid, grid[::-1]]),
        y=np.concatenate([np.maximum(delta, 0), np.minimum(delta, 0)[::-1]]),
        fill="toself",
        fillcolor="rgba(0,255,0,0.15)",
        line=dict(width=0),
        name=f"{name_a} ahead"
    ))

    fig.add_trace(go.Scatter(
        x=np.concatenate([grid, grid[::-1]]),
        y=np.concatenate([np.minimum(delta, 0), np.zeros_like(delta)[::-1]]),
        fill="toself",
        fillcolor="rgba(255,0,0,0.15)",
        line=dict(width=0),
        name=f"{name_b} ahead"
    ))

    # Zero line (reference)
    fig.add_hline(y=0, line=dict(color="gray", width=1, dash="dot"))

    # More readable layout
    fig.update_layout(
        title=f"Delta Time — {name_a} vs {name_b}",
        xaxis_title="Distance (m)",
        yaxis_title="Δ Time (s)",
        height=450,
        margin=dict(l=10, r=10, t=50, b=10),
        template="plotly_dark",
        legend=dict(orientation="h", y=-0.15),
    )

    return fig

# -------------------- UI --------------------
st.title("🏎️ F1 Telemetry — Compare")

with st.sidebar:
    year = st.selectbox("Season", list(range(2025, 2019, -1)), index=0)
    sched = get_schedule(year)
    gp = st.selectbox("Grand Prix", sched["EventName"].tolist())

    # session: Race or Qualifying
    kind_label = st.radio("Session", ["Race (R)", "Qualifying (Q)"], horizontal=True, index=0)
    kind_code = "R" if kind_label.startswith("Race") else "Q"

    row = sched[sched["EventName"] == gp].iloc[0]
    rnd = int(row["RoundNumber"]) if "RoundNumber" in sched.columns else int(row.get("EventNumber", 1))

    st.markdown("---")
    st.caption("Primary Lap (Driver A)")
    driver_a = st.text_input("Driver A (e.g., VER, LEC, HAM)", value="VER").upper()
    which_a = st.text_input("Lap A (number) or 'best'", value="best").lower()

    st.markdown("---")
    st.caption("Compare (Driver B)")
    driver_b = st.text_input("Driver B (e.g., LEC, HAM, NOR)", value="LEC").upper()
    which_b = st.text_input("Lap B (number) or 'best'", value="best").lower()

st.subheader(f"{year} {gp} — Round {rnd} — Session: {'Race' if kind_code=='R' else 'Qualifying'}")

with st.spinner("Loading telemetry…"):
    sess = load_session(year, rnd, kind_code)

# Driver A
lap_a = pick_lap(sess, driver_a, which_a)
if lap_a is None:
    st.error(f"Nessun giro valido per {driver_a} ({which_a}). Prova 'best' o un altro numero di giro.")
    st.stop()
tel_a = lap_telemetry(lap_a)

# Driver B
lap_b = pick_lap(sess, driver_b, which_b)
tel_b = lap_telemetry(lap_b) if lap_b is not None else None

# ---- KPI at the top (A and B side by side)
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Driver A", driver_a)
c2.metric("Lap A", str(int(lap_a["LapNumber"])))
c3.metric("Best Lap A", fmt_laptime(sess.laps.pick_drivers([driver_a]).pick_fastest()["LapTime"]))

if lap_b is not None:
    c4.metric("Driver B", driver_b)
    c5.metric("Lap B", str(int(lap_b["LapNumber"])))
    c6.metric("Best Lap B", fmt_laptime(sess.laps.pick_drivers([driver_b]).pick_fastest()["LapTime"]))
else:
    c4.metric("Driver B", "—")
    c5.metric("Lap B", "—")
    c6.metric("Best Lap B", "—")

# ---- Tabs: Telemetry / Delta
tab_tel, tab_delta = st.tabs(["📈 Telemetry", "🆚 Delta"])

with tab_tel:
    fig = overlay_telemetry_fig(
        tel_a, tel_b,
        f"{driver_a} L{int(lap_a['LapNumber'])}",
        f"{driver_b} L{int(lap_b['LapNumber'])}" if lap_b is not None else "—",
        title="Speed / Throttle / Brake / Gear vs Distance"
    )
    st.plotly_chart(fig, use_container_width=True)

with tab_delta:
    if tel_b is None:
        st.info("Seleziona un Driver B per il confronto (delta).")
    else:
        figd = delta_time_fig(
            tel_a, tel_b,
            f"{driver_a} L{int(lap_a['LapNumber'])}",
            f"{driver_b} L{int(lap_b['LapNumber'])}"
        )
        st.plotly_chart(figd, use_container_width=True)