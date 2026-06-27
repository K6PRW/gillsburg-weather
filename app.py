from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

APP_DIR = Path(__file__).resolve().parent
LOCATIONS_FILE = APP_DIR / "locations.csv"
WEATHERLINK_BASE = "https://api.weatherlink.com/v2"
OPENWEATHER_CURRENT = "https://api.openweathermap.org/data/2.5/weather"
REQUEST_TIMEOUT = 20


@dataclass
class Conditions:
    name: str
    source: str
    region: str
    latitude: float
    longitude: float
    observed_at: datetime | None = None
    wind_mph: float | None = None
    gust_mph: float | None = None
    wind_degrees: float | None = None
    rain_in: float | None = None
    pressure_inhg: float | None = None
    temperature_f: float | None = None
    humidity_pct: float | None = None
    description: str | None = None
    error: str | None = None


def secret(name: str, default: Any = "") -> Any:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def first_value(records: Iterable[dict[str, Any]], keys: Iterable[str]) -> float | None:
    for record in records:
        for key in keys:
            if key in record:
                value = safe_float(record.get(key))
                if value is not None:
                    return value
    return None


def wind_compass(degrees: float | None) -> str:
    if degrees is None:
        return "—"
    points = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return points[int((degrees + 11.25) // 22.5) % 16]


def fmt(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def observation_age(observed_at: datetime | None) -> str:
    if observed_at is None:
        return "Unknown update time"
    seconds = max(0, int(time.time() - observed_at.timestamp()))
    if seconds < 60:
        return "Updated just now"
    if seconds < 3600:
        return f"Updated {seconds // 60} min ago"
    return f"Updated {seconds // 3600} hr ago"


@st.cache_data(ttl=300, show_spinner=False)
def weatherlink_stations(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{WEATHERLINK_BASE}/stations",
        params={"api-key": api_key},
        headers={"X-Api-Secret": api_secret},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("stations", [])


@st.cache_data(ttl=240, show_spinner=False)
def weatherlink_current(api_key: str, api_secret: str, station_id: int) -> dict[str, Any]:
    response = requests.get(
        f"{WEATHERLINK_BASE}/current/{station_id}",
        params={"api-key": api_key},
        headers={"X-Api-Secret": api_secret},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def flatten_weatherlink_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for sensor in payload.get("sensors", []):
        data = sensor.get("data", [])
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            for record in data:
                if isinstance(record, dict):
                    enriched = dict(record)
                    enriched["_sensor_type"] = sensor.get("sensor_type")
                    enriched["_data_structure_type"] = sensor.get("data_structure_type")
                    records.append(enriched)
    return records


def parse_weatherlink(name: str, region: str, latitude: float, longitude: float, payload: dict[str, Any]) -> Conditions:
    records = flatten_weatherlink_records(payload)
    timestamps = [safe_float(r.get("ts")) for r in records]
    timestamp = max((t for t in timestamps if t is not None), default=None)

    # Davis field names vary by console/sensor type. These ordered candidates
    # cover the common Vantage Pro2/ISS and barometer records.
    wind_mph = first_value(records, ["wind_speed_last", "wind_speed_avg_last_1_min", "wind_speed_hi_last_10_min", "wind_speed"])
    gust_mph = first_value(records, ["wind_speed_hi_last_10_min", "wind_speed_hi_last_2_min", "wind_gust", "wind_speed_hi"])
    wind_degrees = first_value(records, ["wind_dir_last", "wind_dir_scalar_avg_last_1_min", "wind_dir_at_hi_speed_last_10_min", "wind_dir"])
    rain_in = first_value(records, ["rainfall_daily_in", "rainfall_last_15_min_in", "rainfall_last_60_min_in", "rainfall_last_24_hr_in", "rainfall_in"])
    pressure_inhg = first_value(records, ["bar_sea_level", "bar_absolute", "pressure_in", "barometer"])
    temperature_f = first_value(records, ["temp", "temp_out", "temperature"])
    humidity_pct = first_value(records, ["hum", "hum_out", "humidity"])

    return Conditions(
        name=name,
        source="Davis WeatherLink",
        region=region,
        latitude=latitude,
        longitude=longitude,
        observed_at=datetime.fromtimestamp(timestamp) if timestamp else None,
        wind_mph=wind_mph,
        gust_mph=gust_mph,
        wind_degrees=wind_degrees,
        rain_in=rain_in,
        pressure_inhg=pressure_inhg,
        temperature_f=temperature_f,
        humidity_pct=humidity_pct,
    )


@st.cache_data(ttl=600, show_spinner=False)
def openweather_current(api_key: str, latitude: float, longitude: float) -> dict[str, Any]:
    response = requests.get(
        OPENWEATHER_CURRENT,
        params={"lat": latitude, "lon": longitude, "appid": api_key, "units": "imperial"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def parse_openweather(name: str, region: str, latitude: float, longitude: float, payload: dict[str, Any]) -> Conditions:
    wind = payload.get("wind", {})
    main = payload.get("main", {})
    rain = payload.get("rain", {})
    weather = payload.get("weather", [])
    # OpenWeather rain is the accumulation over the previous 1 or 3 hours in mm.
    rain_mm = safe_float(rain.get("1h"))
    if rain_mm is None:
        rain_mm = safe_float(rain.get("3h"))
    rain_in = rain_mm / 25.4 if rain_mm is not None else 0.0
    pressure_hpa = safe_float(main.get("sea_level")) or safe_float(main.get("pressure"))
    pressure_inhg = pressure_hpa * 0.0295299830714 if pressure_hpa is not None else None
    timestamp = safe_float(payload.get("dt"))

    return Conditions(
        name=name,
        source="OpenWeather",
        region=region,
        latitude=latitude,
        longitude=longitude,
        observed_at=datetime.fromtimestamp(timestamp) if timestamp else None,
        wind_mph=safe_float(wind.get("speed")),
        gust_mph=safe_float(wind.get("gust")),
        wind_degrees=safe_float(wind.get("deg")),
        rain_in=rain_in,
        pressure_inhg=pressure_inhg,
        temperature_f=safe_float(main.get("temp")),
        humidity_pct=safe_float(main.get("humidity")),
        description=(weather[0].get("description", "").title() if weather else None),
    )


def load_locations() -> pd.DataFrame:
    required = {"display_name", "latitude", "longitude", "region", "source"}
    frame = pd.read_csv(LOCATIONS_FILE)
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"locations.csv is missing: {', '.join(sorted(missing))}")
    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="raise")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="raise")
    frame["source"] = frame["source"].str.lower().str.strip()
    return frame


def card(condition: Conditions, featured: bool = False) -> None:
    border = "2px solid #245b7a" if featured else "1px solid rgba(128,128,128,.35)"
    background = "rgba(36,91,122,.08)" if featured else "rgba(128,128,128,.04)"
    status = condition.description or condition.source
    wind_direction = f"{wind_compass(condition.wind_degrees)} {fmt(condition.wind_degrees, 0)}°" if condition.wind_degrees is not None else "—"
    gust = fmt(condition.gust_mph)
    rain_note = "Today" if condition.source.startswith("Davis") else "Recent interval"
    error_html = f'<div class="error">{condition.error}</div>' if condition.error else ""
    st.markdown(
        f"""
        <div class="station-card" style="border:{border};background:{background}">
          <div class="station-title">{condition.name}</div>
          <div class="station-subtitle">{condition.region} · {status}</div>
          {error_html}
          <div class="metric-grid">
            <div><span>WIND</span><strong>{fmt(condition.wind_mph)} mph</strong><small>{wind_direction}</small></div>
            <div><span>GUST</span><strong>{gust} mph</strong><small>Current reported gust</small></div>
            <div><span>RAIN</span><strong>{fmt(condition.rain_in, 2)} in</strong><small>{rain_note}</small></div>
            <div><span>PRESSURE</span><strong>{fmt(condition.pressure_inhg, 2)} inHg</strong><small>Sea-level when available</small></div>
          </div>
          <div class="station-footer">{observation_age(condition.observed_at)} · {condition.source}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Gillsburg Weather Perimeter", page_icon="🌧️", layout="wide")
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1450px;}
        .station-card {border-radius: 14px; padding: 16px; margin: 8px 0 16px; min-height: 260px;}
        .station-title {font-size: 1.45rem; font-weight: 750; line-height: 1.15;}
        .station-subtitle {opacity: .72; margin-top: 4px; margin-bottom: 14px;}
        .metric-grid {display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px;}
        .metric-grid div {padding:10px; border-radius:10px; background:rgba(128,128,128,.08);}
        .metric-grid span {display:block; font-size:.72rem; letter-spacing:.08em; opacity:.65;}
        .metric-grid strong {display:block; font-size:1.45rem; margin-top:3px;}
        .metric-grid small {display:block; opacity:.65; margin-top:2px;}
        .station-footer {font-size:.82rem; opacity:.65; margin-top:14px;}
        .error {padding:8px; border-radius:8px; background:rgba(190,40,40,.13); margin-bottom:12px;}
        @media (max-width: 640px) {
          .block-container {padding-left:.75rem; padding-right:.75rem;}
          .metric-grid {grid-template-columns:1fr 1fr; gap:8px;}
          .metric-grid strong {font-size:1.22rem;}
          .station-card {min-height:auto;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("Gillsburg Weather Perimeter")
    st.caption("Current wind, gust, rain and barometric pressure — home station centered with Gulf Coast monitoring points.")

    refresh_minutes = st.sidebar.select_slider("Refresh interval", options=[5, 10, 15, 30], value=10)
    st_autorefresh(interval=refresh_minutes * 60 * 1000, key="weather_refresh")
    if st.sidebar.button("Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    try:
        locations = load_locations()
    except Exception as exc:
        st.error(f"Could not read locations.csv: {exc}")
        st.stop()

    openweather_key = str(secret("OPENWEATHER_API_KEY", "")).strip()
    wl_key = str(secret("WEATHERLINK_API_KEY", "")).strip()
    wl_secret = str(secret("WEATHERLINK_API_SECRET", "")).strip()
    configured_station_id = int(secret("WEATHERLINK_STATION_ID", 0) or 0)

    conditions: list[Conditions] = []
    discovered_station_id: int | None = None
    discovered_station_name: str | None = None

    for row in locations.itertuples(index=False):
        if row.source == "weatherlink":
            if not wl_key or not wl_secret:
                conditions.append(Conditions(row.display_name, "Davis WeatherLink", row.region, row.latitude, row.longitude, error="WeatherLink credentials have not been entered in secrets.toml."))
                continue
            try:
                station_id = configured_station_id
                if station_id <= 0:
                    stations = weatherlink_stations(wl_key, wl_secret)
                    if not stations:
                        raise RuntimeError("No stations are available to this WeatherLink API account.")
                    station_id = int(stations[0]["station_id"])
                    discovered_station_id = station_id
                    discovered_station_name = str(stations[0].get("station_name", "WeatherLink station"))
                payload = weatherlink_current(wl_key, wl_secret, station_id)
                conditions.append(parse_weatherlink(row.display_name, row.region, row.latitude, row.longitude, payload))
            except requests.HTTPError as exc:
                message = f"WeatherLink request failed ({exc.response.status_code}). Check the API key, secret and station permission."
                conditions.append(Conditions(row.display_name, "Davis WeatherLink", row.region, row.latitude, row.longitude, error=message))
            except Exception as exc:
                conditions.append(Conditions(row.display_name, "Davis WeatherLink", row.region, row.latitude, row.longitude, error=str(exc)))
        elif row.source == "openweather":
            if not openweather_key:
                conditions.append(Conditions(row.display_name, "OpenWeather", row.region, row.latitude, row.longitude, error="OpenWeather API key has not been entered in secrets.toml."))
                continue
            try:
                payload = openweather_current(openweather_key, row.latitude, row.longitude)
                conditions.append(parse_openweather(row.display_name, row.region, row.latitude, row.longitude, payload))
            except requests.HTTPError as exc:
                message = f"OpenWeather request failed ({exc.response.status_code}). Check that the new API key is active."
                conditions.append(Conditions(row.display_name, "OpenWeather", row.region, row.latitude, row.longitude, error=message))
            except Exception as exc:
                conditions.append(Conditions(row.display_name, "OpenWeather", row.region, row.latitude, row.longitude, error=str(exc)))

    home = [c for c in conditions if c.source.startswith("Davis")]
    perimeter = [c for c in conditions if not c.source.startswith("Davis")]

    st.subheader("Home conditions")
    if home:
        card(home[0], featured=True)
    else:
        st.warning("No Davis home-station row was found in locations.csv.")

    st.subheader("Gulf Coast perimeter")
    columns = st.columns(3)
    for index, condition in enumerate(perimeter):
        with columns[index % 3]:
            card(condition)

    with st.sidebar:
        st.divider()
        st.caption("Data notes")
        st.caption("Davis rain is daily rainfall when that field is available. OpenWeather rain is the most recent 1-hour or 3-hour accumulation reported by the API.")
        st.caption("Use official NWS alerts and warnings for safety decisions. This dashboard is for situational awareness.")
        if discovered_station_id:
            st.success(f"WeatherLink station found: {discovered_station_name}\n\nStation ID: {discovered_station_id}")
            st.caption("Enter this number as WEATHERLINK_STATION_ID in secrets.toml to lock the app to this station.")


if __name__ == "__main__":
    main()
