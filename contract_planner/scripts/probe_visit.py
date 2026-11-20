"""Non-commercial education probe. Only a city centroid is sent to Open-Meteo."""

import json
from datetime import datetime, timezone
from pathlib import Path
import requests

root = Path(__file__).resolve().parents[1]
rows = []
geo = {}
for name in ("city", "weather"):
    try:
        if name == "city":
            url = "https://geocoding-api.open-meteo.com/v1/search"
            params = {
                "name": "Daejeon",
                "count": 3,
                "language": "en",
                "countryCode": "KR",
            }
        else:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "daily": "precipitation_probability_max",
                "timezone": "Asia/Seoul",
                "forecast_days": 7,
            }
        response = requests.get(url, params=params, timeout=12)
        response.raise_for_status()
        data = response.json()
        if name == "city":
            geo = next(x for x in data["results"] if x["country_code"] == "KR")
            count = len(data["results"])
        else:
            count = len(data["daily"]["time"])
            assert count and any(
                x is not None for x in data["daily"]["precipitation_probability_max"]
            )
        (root / f"fixtures/{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        status = "verified"
    except Exception as exc:
        status, count = type(exc).__name__, 0
    rows.append(
        dict(
            api=name,
            status=status,
            rows=count,
            at=datetime.now(timezone.utc).isoformat(),
        )
    )
    print(rows[-1])
(root / "docs/visit-probes.json").write_text(
    json.dumps(rows, indent=2), encoding="utf-8"
)
