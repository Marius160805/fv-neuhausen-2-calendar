from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
import re

import requests
from bs4 import BeautifulSoup

TEAM_ID = "011MIFF6OG000000VTVG0001VTR8C1K7"
TEAM_NAME = "SG Bettringen 1885 e.V."
BASE_URL = "https://www.fussball.de"
NEXT_URL = f"{BASE_URL}/ajax.team.next.games/-/mode/PAGE/team-id/{TEAM_ID}"
PREV_URL = f"{BASE_URL}/ajax.team.prev.games/-/mode/PAGE/team-id/{TEAM_ID}"
OUTPUT = Path("sg-bettringen.ics")
BERLIN = ZoneInfo("Europe/Berlin")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; SG-Bettringen-Calendar/1.0)",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
})


def fetch(url: str) -> str:
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def parse_info(text: str) -> tuple[datetime, str]:
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}:\d{2})\s*Uhr", text)
    if not match:
        raise ValueError(f"Datum/Uhrzeit nicht erkannt: {text!r}")

    local_dt = datetime.strptime(
        f"{match.group(1)} {match.group(2)}", "%d.%m.%Y %H:%M"
    ).replace(tzinfo=BERLIN)

    parts = [p.strip() for p in text.split("|") if p.strip()]
    competition = parts[-1] if len(parts) > 1 else ""
    return local_dt, competition


def parse_games(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    games: list[dict] = []
    current_dt: datetime | None = None
    current_comp = ""

    for row in soup.find_all("tr"):
        classes = row.get("class", [])

        if "visible-small" in classes:
            cell = row.find("td")
            if cell:
                try:
                    current_dt, current_comp = parse_info(cell.get_text(" ", strip=True))
                except ValueError:
                    current_dt, current_comp = None, ""
            continue

        score_cell = row.find("td", class_="column-score")
        if not score_cell or current_dt is None:
            continue

        home_cell = row.find("td", class_="column-club-left")
        away_cell = row.find("td", class_="column-club-right")

        if not home_cell or not away_cell:
            club_cells = row.find_all("td", class_="column-club")
            if len(club_cells) == 2:
                home_cell, away_cell = club_cells
            else:
                continue

        home_tag = home_cell.find(class_="club-name")
        away_tag = away_cell.find(class_="club-name")
        if not home_tag or not away_tag:
            continue

        home = home_tag.get_text(" ", strip=True)
        away = away_tag.get_text(" ", strip=True)

        link = score_cell.find("a", href=True)
        game_url = urljoin(BASE_URL, link["href"]) if link else ""
        game_id = game_url.rstrip("/").split("/")[-1] if game_url else ""

        if not game_id:
            game_id = f"{current_dt.isoformat()}-{home}-{away}"

        games.append({
            "id": game_id,
            "start": current_dt,
            "competition": current_comp,
            "home": home,
            "away": away,
            "url": game_url,
            "location": "",
        })

    return games


def enrich_location(game: dict) -> None:
    if not game["url"]:
        return
    try:
        soup = BeautifulSoup(fetch(game["url"]), "lxml")
        stage = soup.find("section", id="stage")
        location = stage.find("a", class_="location") if stage else None
        if location:
            game["location"] = location.get_text(" ", strip=True).replace(
                "Rasenplatz, ", ""
            ).strip()
    except requests.RequestException:
        pass


def ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold(line: str, limit: int = 73) -> list[str]:
    if len(line.encode("utf-8")) <= 75:
        return [line]

    result: list[str] = []
    current = ""
    prefix = ""

    for char in line:
        candidate = current + char
        if len((prefix + candidate).encode("utf-8")) > limit and current:
            result.append(prefix + current)
            current = char
            prefix = " "
        else:
            current = candidate

    if current:
        result.append(prefix + current)

    return result


def build_ics(games: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Marius160805//SG Bettringen Kalender//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:SG Bettringen",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]

    for game in sorted(games, key=lambda g: g["start"]):
        start_utc = game["start"].astimezone(timezone.utc)
        end_utc = start_utc + timedelta(hours=2)
        summary = f"⚽ {game['home']} – {game['away']}"

        description_parts = []
        if game["competition"]:
            description_parts.append(game["competition"])
        if game["url"]:
            description_parts.append(game["url"])

        event_lines = [
            "BEGIN:VEVENT",
            f"UID:{ics_escape(game['id'])}@fussball.de",
            f"DTSTAMP:{now}",
            f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{ics_escape(summary)}",
        ]

        if game["location"]:
            event_lines.append(f"LOCATION:{ics_escape(game['location'])}")

        if description_parts:
            event_lines.append(
                f"DESCRIPTION:{ics_escape(chr(10).join(description_parts))}"
            )

        if game["url"]:
            event_lines.append(f"URL:{game['url']}")

        event_lines.append("END:VEVENT")
        lines.extend(event_lines)

    lines.append("END:VCALENDAR")

    folded: list[str] = []
    for line in lines:
        folded.extend(fold(line))

    return "\r\n".join(folded) + "\r\n"


def main() -> None:
    games_by_id: dict[str, dict] = {}

    for url in (PREV_URL, NEXT_URL):
        for game in parse_games(fetch(url)):
            games_by_id[game["id"]] = game

    games = list(games_by_id.values())

    if not games:
        raise RuntimeError(
            "Keine Spiele gefunden; vorhandene sg-bettringen.ics wird nicht überschrieben."
        )

    for game in games:
        enrich_location(game)

    OUTPUT.write_text(build_ics(games), encoding="utf-8", newline="")
    print(f"{len(games)} SG-Bettringen-Spiele nach {OUTPUT} geschrieben.")


if __name__ == "__main__":
    main()
