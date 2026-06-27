from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import altair as alt
import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium


st.set_page_config(
    page_title="Gillsburg Weather",
    page_icon="🌦️",
    layout="wide",
)

WEATHERLINK_BASE = "https://api.weatherlink.com/v2"
OPENWEATHER_BASE = "https://api.openweathermap.org/data/4.0/onecall"
RAINVIEWER_INDEX = "https://api.rainviewer.com/public/weather-maps.json"


def secret(name: str, default: Any = None) -> Any:
    try:
        return st.secrets[name]
    except Exception:
        return default


WEATHERLINK_API_KEY = secret("WEATHERLINK_API_KEY")
WEATHERLINK_API_SECRET = secret("WEATHERLINK_API_SECRET")
WEATHERLINK_STATION_ID = secret("WEATHERLINK_STATION_ID")
OPENWEATHER_API_KEY = secret("OPENWEATHER_API_KEY")
FALLBACK_LAT = secret("GILLSBURG_LAT")
FALLBACK_LON = secret("GILLSBURG_LON")


def require_secrets() -> None:
    missing = [
        name
        for name, value in {
            "WEATHERLINK_API_KEY": WEATHERLINK_API_KEY,
            "WEATHERLINK_API_SECRET": WEATHERLINK_API_SECRET,
            "WEATHERLINK_STATION_ID": WEATHERLINK_STATION_ID,
            "OPENWEATHER_API_KEY": OPENWEATHER_API_KEY,
        }.items()
        if not value
    ]
    if missing:
        st.error("Missing Streamlit secrets: " + ", ".join(missing))
        st.stop()


def get_json(url: str, *, params: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None, timeout: int = 20) -> dict[str, Any]:
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=600, show_spinner=False)
def get_weatherlink_stations(api_key: str, api_secret: str) -> dict[str, Any]:
    return get_json(
        f"{WEATHERLINK_BASE}/stations",
        params={"api-key": api_key},
        headers={"X-Api-Secret": api_secret},
    )


@st.cache_data(ttl=60, show_spinner=False)
def get_weatherlink_current(api_key: str, api_secret: str, station_id: str) -> dict[str, Any]:
    return get_json(
        f"{WEATHERLINK_BASE}/current/{station_id}",
        params={"api-key": api_key},
        headers={"X-Api-Secret": api_secret},
    )


@st.cache_data(ttl=600, show_spinner=False)
def get_openweather_current(api_key: str, lat: float, lon: float) -> dict[str, Any]:
    return get_json(
        f"{OPENWEATHER_BASE}/current",
        params={"lat": lat, "lon": lon, "units": "imperial", "lang": "en", "appid": api_key},
    )


@st.cache_data(ttl=600, show_spinner=False)
def get_openweather_15min(api_key: str, lat: float, lon: float) -> dict[str, Any]:
    return get_json(
        f"{OPENWEATHER_BASE}/timeline/15min",
        params={"lat": lat, "lon": lon, "units": "imperial", "lang": "en", "appid": api_key},
    )


@st.cache_data(ttl=21600, show_spinner=False)
def get_openweather_daily(api_key: str, lat: float, lon: float) -> dict[str, Any]:
    return get_json(
        f"{OPENWEATHER_BASE}/timeline/1day",
        params={"lat": lat, "lon": lon, "units": "imperial", "lang": "en", "appid": api_key},
    )


@st.cache_data(ttl=600, show_spinner=False)
def get_rainviewer_index() -> dict[str, Any]:
    return get_json(RAINVIEWER_INDEX)


def flatten_weatherlink_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for sensor in payload.get("sensors", []):
        for item in sensor.get("data", []):
            if isinstance(item, dict):
                records.append(item)
    return records


def first_value(records: Iterable[dict[str, Any]], *keys: str) -> Any:
    for key in keys:
        for record in records:
            value = record.get(key)
            if value is not None:
                return value
    return None


def fmt_number(value: Any, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return f"{value}{suffix}"


def cardinal(degrees: Any) -> str:
    try:
        deg = float(degrees) % 360
    except (TypeError, ValueError):
        return "—"
    points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return points[round(deg / 45) % 8]


def find_station_metadata(payload: dict[str, Any], station_id: str) -> dict[str, Any]:
    for station in payload.get("stations", []):
        if str(station.get("station_id")) == str(station_id) or str(station.get("station_id_uuid")) == str(station_id):
            return station
    return {}


def station_coordinates(station: dict[str, Any]) -> tuple[float | None, float | None]:
    lat_keys = ("latitude", "lat", "station_latitude")
    lon_keys = ("longitude", "lon", "station_longitude")
    lat = next((station.get(k) for k in lat_keys if station.get(k) is not None), FALLBACK_LAT)
    lon = next((station.get(k) for k in lon_keys if station.get(k) is not None), FALLBACK_LON)
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def openweather_record(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", [])
    return data[0] if data else {}


def weather_description(record: dict[str, Any]) -> str:
    weather = record.get("weather") or []
    if weather and isinstance(weather[0], dict):
        return str(weather[0].get("description", "Unknown")).title()
    return "Unknown"


require_secrets()

st.title("Gillsburg Weather")
st.caption("Live Davis WeatherLink conditions, OpenWeather forecasts, and current radar.")

if st.sidebar.button("Refresh now", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

page = st.sidebar.radio("View", ["Home", "Forecast", "Radar"], index=0)

try:
    stations_payload = get_weatherlink_stations(
        str(WEATHERLINK_API_KEY), str(WEATHERLINK_API_SECRET)
    )
    station = find_station_metadata(stations_payload, str(WEATHERLINK_STATION_ID))
    lat, lon = station_coordinates(station)
except requests.RequestException as exc:
    st.error(f"Could not retrieve WeatherLink station information: {exc}")
    st.stop()

if lat is None or lon is None:
    st.error(
        "The station coordinates were not found. Add GILLSBURG_LAT and GILLSBURG_LON "
        "to Streamlit Secrets, then restart the app."
    )
    st.stop()

if page == "Home":
    try:
        wl_payload = get_weatherlink_current(
            str(WEATHERLINK_API_KEY),
            str(WEATHERLINK_API_SECRET),
            str(WEATHERLINK_STATION_ID),
        )
        records = flatten_weatherlink_records(wl_payload)
    except requests.RequestException as exc:
        st.error(f"WeatherLink request failed: {exc}")
        records = []

    temp = first_value(records, "temp", "temp_out", "temp_last")
    humidity = first_value(records, "hum", "hum_out", "humidity")
    dew_point = first_value(records, "dew_point", "dew_point_out")
    wind_speed = first_value(records, "wind_speed_last", "wind_speed", "wind_speed_avg_last_10_min")
    wind_dir = first_value(records, "wind_dir_last", "wind_dir")
    gust = first_value(records, "wind_speed_hi_last_10_min", "wind_gust", "wind_speed_hi")
    rain_today = first_value(records, "rainfall_daily", "rain_day", "rainfall_day")
    rain_rate = first_value(records, "rain_rate_last", "rain_rate")
    pressure = first_value(records, "bar_sea_level", "bar_absolute", "bar")
    solar = first_value(records, "solar_rad", "solar_radiation")
    uv = first_value(records, "uv_index", "uv")
    ts = first_value(records, "ts")

    st.subheader(station.get("station_name", "Gillsburg Davis Station"))

    row1 = st.columns(4)
    row1[0].metric("Temperature", fmt_number(temp, " °F"))
    row1[1].metric("Humidity", fmt_number(humidity, " %", 0))
    row1[2].metric("Dew point", fmt_number(dew_point, " °F"))
    row1[3].metric("Pressure", fmt_number(pressure, " inHg", 2))

    row2 = st.columns(4)
    row2[0].metric("Wind", fmt_number(wind_speed, " mph"), cardinal(wind_dir))
    row2[1].metric("Gust", fmt_number(gust, " mph"))
    row2[2].metric("Rain today", fmt_number(rain_today, " in", 2))
    row2[3].metric("Rain rate", fmt_number(rain_rate, " in/hr", 2))

    row3 = st.columns(2)
    row3[0].metric("Solar radiation", fmt_number(solar, " W/m²", 0))
    row3[1].metric("UV index", fmt_number(uv, "", 1))

    if ts:
        try:
            updated = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone()
            st.caption(f"WeatherLink updated {updated:%b %d, %Y at %I:%M:%S %p %Z}")
        except Exception:
            pass

    st.divider()
    try:
        ow_current = openweather_record(
            get_openweather_current(str(OPENWEATHER_API_KEY), lat, lon)
        )
        st.subheader("OpenWeather outlook")
        a, b, c, d = st.columns(4)
        a.metric("Feels like", fmt_number(ow_current.get("feels_like"), " °F"))
        b.metric("Cloud cover", fmt_number(ow_current.get("clouds"), " %", 0))
        c.metric("Visibility", fmt_number((ow_current.get("visibility") or 0) / 1609.344, " mi"))
        d.metric("UV index", fmt_number(ow_current.get("uvi"), "", 1))
        st.info(weather_description(ow_current))
    except requests.RequestException as exc:
        st.warning(f"OpenWeather current conditions are unavailable: {exc}")

elif page == "Forecast":
    try:
        min_payload = get_openweather_15min(str(OPENWEATHER_API_KEY), lat, lon)
        min_records = min_payload.get("data", [])
        min_df = pd.DataFrame(min_records)
        if not min_df.empty:
            min_df["time"] = pd.to_datetime(min_df["dt"], unit="s", utc=True).dt.tz_convert(
                min_payload.get("timezone", "America/Chicago")
            )
            min_df["rain chance"] = min_df.get("pop", 0) * 100

            st.subheader("Next 12 hours")
            chart = (
                alt.Chart(min_df.head(48))
                .mark_line(point=False)
                .encode(
                    x=alt.X("time:T", title="Time"),
                    y=alt.Y("temp:Q", title="Temperature (°F)"),
                    tooltip=[
                        alt.Tooltip("time:T", title="Time"),
                        alt.Tooltip("temp:Q", title="Temp", format=".1f"),
                        alt.Tooltip("rain chance:Q", title="Rain chance", format=".0f"),
                    ],
                )
                .properties(height=320)
            )
            st.altair_chart(chart, use_container_width=True)

            rain_chart = (
                alt.Chart(min_df.head(48))
                .mark_area(opacity=0.4)
                .encode(
                    x=alt.X("time:T", title="Time"),
                    y=alt.Y("rain chance:Q", title="Rain chance (%)", scale=alt.Scale(domain=[0, 100])),
                    tooltip=[
                        alt.Tooltip("time:T", title="Time"),
                        alt.Tooltip("rain chance:Q", title="Rain chance", format=".0f"),
                    ],
                )
                .properties(height=220)
            )
            st.altair_chart(rain_chart, use_container_width=True)

        daily_payload = get_openweather_daily(str(OPENWEATHER_API_KEY), lat, lon)
        daily = daily_payload.get("data", [])
        st.subheader("Daily forecast")
        cols = st.columns(min(5, len(daily)))
        tz_name = daily_payload.get("timezone", "America/Chicago")
        for idx, day in enumerate(daily[:5]):
            local_date = pd.to_datetime(day["dt"], unit="s", utc=True).tz_convert(tz_name)
            temp_obj = day.get("temp", {}) if isinstance(day.get("temp"), dict) else {}
            with cols[idx]:
                st.markdown(f"**{local_date:%A}**")
                st.write(weather_description(day))
                st.metric("High", fmt_number(temp_obj.get("max"), " °F"))
                st.metric("Low", fmt_number(temp_obj.get("min"), " °F"))
                st.write(f"Rain chance: {fmt_number((day.get('pop') or 0) * 100, '%', 0)}")
    except requests.RequestException as exc:
        st.error(f"OpenWeather forecast request failed: {exc}")

    st.caption("Weather forecast data © OpenWeather")

else:
    st.subheader("Current radar")
    st.caption("Latest available radar frame centered on the Gillsburg station.")

    try:
        radar_index = get_rainviewer_index()
        host = radar_index["host"]
        frames = radar_index.get("radar", {}).get("past", [])
        if not frames:
            raise RuntimeError("No radar frames were returned.")
        latest = frames[-1]
        tile_url = f"{host}{latest['path']}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"

        radar_map = folium.Map(
            location=[lat, lon],
            zoom_start=7,
            tiles="OpenStreetMap",
            control_scale=True,
        )
        folium.TileLayer(
            tiles=tile_url,
            attr="Radar © RainViewer; map © OpenStreetMap contributors",
            name="Current radar",
            overlay=True,
            opacity=0.75,
        ).add_to(radar_map)
        folium.Marker(
            [lat, lon],
            tooltip=station.get("station_name", "Gillsburg"),
            icon=folium.Icon(color="red", icon="home"),
        ).add_to(radar_map)
        folium.LayerControl().add_to(radar_map)

        st_folium(radar_map, use_container_width=True, height=600)
        frame_time = datetime.fromtimestamp(int(latest["time"]), tz=timezone.utc).astimezone()
        st.caption(f"Radar frame: {frame_time:%b %d, %Y at %I:%M %p %Z}")
    except Exception as exc:
        st.error(f"Radar is unavailable: {exc}")

st.divider()
st.caption("WeatherLink data from Davis Instruments. Forecast data © OpenWeather. Radar © RainViewer.")
