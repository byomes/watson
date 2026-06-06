"""jobs/monitoring/weather_every_morning.py — daily 6am weather forecast via Telegram."""
import asyncio
import os
from datetime import date

import requests
from dotenv import load_dotenv
from telegram import Bot

load_dotenv()

LAT = 39.7447
LON = -75.5484
TIMEZONE = "America/New_York"

_WMO = {
    0:  "☀️ Clear",
    1:  "⛅ Partly cloudy", 2:  "⛅ Partly cloudy", 3:  "⛅ Partly cloudy",
    45: "🌫 Foggy",         48: "🌫 Foggy",
    51: "🌧 Rainy",         53: "🌧 Rainy",         55: "🌧 Rainy",
    61: "🌧 Rainy",         63: "🌧 Rainy",         65: "🌧 Rainy",
    71: "❄️ Snowy",         73: "❄️ Snowy",         75: "❄️ Snowy",  77: "❄️ Snowy",
    80: "🌦 Showers",       81: "🌦 Showers",       82: "🌦 Showers",
    95: "⛈ Thunderstorms", 96: "⛈ Thunderstorms", 99: "⛈ Thunderstorms",
}
_RAIN_CODES = {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99}
_SNOW_CODES = {71, 73, 75, 77}
_TARGET_HOURS = [6, 8, 10, 12, 14, 16, 18, 20]


def _hour_label(h: int) -> str:
    if h == 0:
        return "12am"
    if h < 12:
        return f"{h}am"
    if h == 12:
        return "12pm"
    return f"{h - 12}pm"


def _fetch() -> dict:
    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": LAT,
            "longitude": LON,
            "hourly": "temperature_2m,precipitation_probability,precipitation,windspeed_10m,weathercode",
            "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max",
            "timezone": TIMEZONE,
            "forecast_days": 1,
            "temperature_unit": "fahrenheit",
            "windspeed_unit": "mph",
            "precipitation_unit": "inch",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _format(data: dict) -> str:
    hourly = data["hourly"]
    daily = data["daily"]

    today = date.today()
    today_str = today.isoformat()
    weekday = today.strftime("%A")
    month = today.strftime("%B")
    day = today.day

    max_temp = daily["temperature_2m_max"][0]
    max_wind = round(daily["windspeed_10m_max"][0])

    # Index today's hours by hour integer
    hour_idx = {}
    for i, t in enumerate(hourly["time"]):
        if t.startswith(today_str):
            hour_idx[int(t[11:13])] = i

    hourly_lines = []
    has_rain = False
    has_snow = False
    max_precip_prob = 0

    for h in _TARGET_HOURS:
        idx = hour_idx.get(h)
        if idx is None:
            continue
        temp = round(hourly["temperature_2m"][idx])
        precip_prob = hourly["precipitation_probability"][idx] or 0
        wcode = hourly["weathercode"][idx]
        condition = _WMO.get(wcode, "🌡")

        if wcode in _RAIN_CODES:
            has_rain = True
        if wcode in _SNOW_CODES:
            has_snow = True
        if precip_prob > max_precip_prob:
            max_precip_prob = precip_prob

        hourly_lines.append(
            f"{_hour_label(h):<5}  {temp:>3}°F  {condition}  {precip_prob}% chance rain"
        )

    # Recommendation
    if max_temp > 80:
        rec = "Light clothing, it'll be warm today"
    elif max_temp < 40:
        rec = "Heavy coat, it's cold today"
    elif max_temp <= 60:
        rec = "Light jacket recommended"
    else:
        rec = "Comfortable layers"

    if max_precip_prob > 50:
        rec += " — bring an umbrella"
    if has_snow:
        rec += " — watch for slippery conditions"

    parts = [
        f"🌤 Good morning, Dr. Bill — {weekday} {month} {day}",
        "",
        "📍 Wilmington, DE",
        "",
        "\n".join(hourly_lines),
        "",
        f"💨 Wind: {max_wind}mph",
    ]
    if has_rain and not has_snow:
        parts.append("🌧 Rain expected — carry an umbrella")
    if has_snow:
        parts.append("❄️ Snow expected — dress warm")
    parts.append(f"\nRecommendation: {rec}")

    return "\n".join(parts)


async def _send(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
    async with Bot(token=token) as bot:
        await bot.send_message(chat_id=chat_id, text=message)


def run() -> str:
    data = _fetch()
    message = _format(data)
    asyncio.run(_send(message))
    return "Weather forecast sent."


if __name__ == "__main__":
    run()
