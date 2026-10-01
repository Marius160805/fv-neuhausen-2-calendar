from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
import os
import re
import unicodedata

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar

TEAM_ID = "011MIB6A68000000VTVG0001VTR8C1K7"
TEAM_NAME = "FV Neuhausen II"
BASE_URL = "https://www.fussball.de"
NEXT_URL = f"{BASE_URL}/ajax.team.next.games/-/mode/PAGE/team-id/{TEAM_ID}"
PREV_URL = f"{BASE_URL}/ajax.team.prev.games/-/mode/PAGE/team-id/{TEAM_ID}"
OUTPUT = Path("spiele.ics")
BERLIN = ZoneInfo("Europe/Berlin")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; FV-Neuhausen-II-Calendar/1.2)",
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
            "kickoff": current_dt,
            "competition": current_comp,
            "home": home,
            "away": away,
            "url": game_url,
            "location": "",
            "meeting_time": None,
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


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def relevant_words(team: str) -> set[str]:
    stop = {
        "fv", "spfr", "sv", "tsv", "tus", "fc", "sg", "vfb", "vfl",
        "ii", "iii", "2", "3", "e", "v", "ev"
    }
    return {
        w for w in normalize_text(team).split()
        if len(w) >= 4 and w not in stop
    }


def as_berlin_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=BERLIN)
        return value.astimezone(BERLIN)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=BERLIN)
    return None


def fetch_spielerplus_events() -> list[dict]:
    raw_url = os.environ.get("SPIELERPLUS_ICS_URL", "").strip()
    if not raw_url:
        print("SPIELERPLUS_ICS_URL fehlt; Treffpunkte werden nicht ergänzt.")
        return []

    url = re.sub(r"^webcal://", "https://", raw_url, flags=re.IGNORECASE)

    try:
        response = SESSION.get(url, timeout=30)
        response.raise_for_status()
        calendar = Calendar.from_ical(response.content)
    except Exception as exc:
        print(f"SpielerPlus konnte nicht gelesen werden: {exc}")
        return []

    events: list[dict] = []

    for component in calendar.walk():
        if component.name != "VEVENT":
            continue

        dtstart_prop = component.get("DTSTART")
        if not dtstart_prop:
            continue

        start = as_berlin_datetime(dtstart_prop.dt)
        if start is None:
            continue

        summary = str(component.get("SUMMARY", "") or "")
        description = str(component.get("DESCRIPTION", "") or "")
        location = str(component.get("LOCATION", "") or "")

        events.append({
            "start": start,
            "summary": summary,
            "description": description,
            "location": location,
            "search": normalize_text(" ".join([summary, description, location])),
        })

    print(f"{len(events)} SpielerPlus-Termine geladen.")
    return events


def find_meeting_time(game: dict, events: list[dict]) -> datetime | None:
    game_date = game["kickoff"].astimezone(BERLIN).date()
    opponent = game["away"] if TEAM_NAME.lower() in game["home"].lower() else game["home"]
    opponent_words = relevant_words(opponent)

    candidates: list[tuple[int, float, dict]] = []

    for event in events:
        if event["start"].date() != game_date:
            continue

        text = event["search"]

        if any(word in text for word in [
            "training", "mannschaftsabend", "besprechung", "sitzung"
        ]):
            continue

        overlap = sum(1 for word in opponent_words if word in text)
        has_game_word = any(word in text for word in [
            "spiel", "punktspiel", "freundschaftsspiel", "testspiel",
            "pokal", "match"
        ])

        minutes_before = (
            game["kickoff"].astimezone(BERLIN) - event["start"]
        ).total_seconds() / 60

        # Ein realistischer Treffpunkt liegt vor dem Anstoß, maximal 6 Stunden vorher.
        if minutes_before < 0 or minutes_before > 360:
            continue

        score = overlap * 10 + (3 if has_game_word else 0)

        # Nicht raten: mindestens Gegnerbezug oder klarer Spielbezug.
        if score == 0:
            continue

        candidates.append((score, abs(minutes_before), event))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]["start"]


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
        "PRODID:-//Marius160805//FV Neuhausen II Kalender//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:FV Neuhausen II",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]

    for game in sorted(games, key=lambda g: g["kickoff"]):
        kickoff_local = game["kickoff"].astimezone(BERLIN)
        event_start_local = game["meeting_time"] or kickoff_local

        # Kalendertermin startet am Treffpunkt. Ohne Treffpunkt fällt er auf Anstoß zurück.
        start_utc = event_start_local.astimezone(timezone.utc)
        # Ende = ca. 2 Stunden nach Anstoß, damit der gesamte Spielblock sichtbar ist.
        end_utc = (kickoff_local + timedelta(hours=2)).astimezone(timezone.utc)

        summary = f"⚽ {game['home']} – {game['away']}"

        description_parts = [
            f"Anpfiff: {kickoff_local.strftime('%H:%M')} Uhr"
        ]

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
            "Keine Spiele gefunden; vorhandene spiele.ics wird nicht überschrieben."
        )

    for game in games:
        enrich_location(game)

    playerplus_events = fetch_spielerplus_events()

    matched = 0
    for game in games:
        meeting_time = find_meeting_time(game, playerplus_events)
        if meeting_time is not None:
            game["meeting_time"] = meeting_time
            matched += 1

    OUTPUT.write_text(build_ics(games), encoding="utf-8", newline="")
    print(
        f"{len(games)} Spiele geschrieben; "
        f"für {matched} Spiele wurde der SpielerPlus-Treffpunkt übernommen."
    )


if __name__ == "__main__":
    main()
