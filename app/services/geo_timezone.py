"""Guess IANA timezone from country / city for outreach local-time stamps."""

from __future__ import annotations

CITY_TZ = {
    "australia|perth": "Australia/Perth",
    "australia|adelaide": "Australia/Adelaide",
    "australia|darwin": "Australia/Darwin",
    "australia|brisbane": "Australia/Brisbane",
    "australia|gold coast": "Australia/Brisbane",
    "australia|cairns": "Australia/Brisbane",
    "australia|hobart": "Australia/Hobart",
    "australia|sydney": "Australia/Sydney",
    "australia|melbourne": "Australia/Melbourne",
    "australia|canberra": "Australia/Sydney",
    "united states|new york": "America/New_York",
    "united states|los angeles": "America/Los_Angeles",
    "united states|chicago": "America/Chicago",
    "united states|denver": "America/Denver",
    "united states|phoenix": "America/Phoenix",
    "canada|vancouver": "America/Vancouver",
    "canada|toronto": "America/Toronto",
    "canada|montreal": "America/Toronto",
    "canada|calgary": "America/Edmonton",
    "canada|edmonton": "America/Edmonton",
    "indonesia|jakarta": "Asia/Jakarta",
    "indonesia|bali": "Asia/Makassar",
    "indonesia|denpasar": "Asia/Makassar",
    "brazil|sao paulo": "America/Sao_Paulo",
    "brazil|são paulo": "America/Sao_Paulo",
    "brazil|manaus": "America/Manaus",
}

COUNTRY_TZ = {
    "Australia": "Australia/Sydney",
    "Bangladesh": "Asia/Dhaka",
    "India": "Asia/Kolkata",
    "United Kingdom": "Europe/London",
    "United States": "America/New_York",
    "United Arab Emirates": "Asia/Dubai",
    "Singapore": "Asia/Singapore",
    "Malaysia": "Asia/Kuala_Lumpur",
    "New Zealand": "Pacific/Auckland",
    "Canada": "America/Toronto",
    "Pakistan": "Asia/Karachi",
    "Sri Lanka": "Asia/Colombo",
    "Philippines": "Asia/Manila",
    "Indonesia": "Asia/Jakarta",
    "Thailand": "Asia/Bangkok",
    "Vietnam": "Asia/Ho_Chi_Minh",
    "South Africa": "Africa/Johannesburg",
    "Germany": "Europe/Berlin",
    "France": "Europe/Paris",
    "Ireland": "Europe/Dublin",
    "Japan": "Asia/Tokyo",
    "South Korea": "Asia/Seoul",
    "China": "Asia/Shanghai",
    "Hong Kong": "Asia/Hong_Kong",
    "Brazil": "America/Sao_Paulo",
    "Mexico": "America/Mexico_City",
    "Saudi Arabia": "Asia/Riyadh",
    "Qatar": "Asia/Qatar",
    "Kuwait": "Asia/Kuwait",
    "Bahrain": "Asia/Bahrain",
    "Oman": "Asia/Muscat",
    "Nepal": "Asia/Kathmandu",
    "Egypt": "Africa/Cairo",
    "Nigeria": "Africa/Lagos",
    "Kenya": "Africa/Nairobi",
    "Turkey": "Europe/Istanbul",
    "Spain": "Europe/Madrid",
    "Italy": "Europe/Rome",
    "Netherlands": "Europe/Amsterdam",
    "Poland": "Europe/Warsaw",
    "Portugal": "Europe/Lisbon",
    "Russia": "Europe/Moscow",
}


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def guess_timezone(country: str | None, city: str | None = None) -> str:
    """Return IANA zone, or empty string if unknown."""
    c = (country or "").strip()
    city_key = f"{_norm(c)}|{_norm(city)}"
    if city_key in CITY_TZ:
        return CITY_TZ[city_key]
    if c in COUNTRY_TZ:
        return COUNTRY_TZ[c]
    for name, tz in COUNTRY_TZ.items():
        if _norm(name) == _norm(c):
            return tz
    return ""
