# FV Neuhausen II – Apple Kalender

Automatisch aktualisierter iCalendar-Feed für die öffentlichen Spiele von **FV Neuhausen II** auf FUSSBALL.DE.

- Quelle: FUSSBALL.DE
- Team-ID: `011MIB6A68000000VTVG0001VTR8C1K7`
- Aktualisierung: täglich per GitHub Actions
- Ausgabe: `spiele.ics`
- PC muss dafür nicht eingeschaltet sein.

## Kalender abonnieren

Diese URL auf dem iPhone als Kalenderabonnement hinzufügen:

`https://raw.githubusercontent.com/Marius160805/fv-neuhausen-2-calendar/main/spiele.ics`

iPhone: **Kalender → Kalender → Kalender hinzufügen → Kalenderabonnement hinzufügen**

## Wie es funktioniert

Die GitHub Action ruft täglich die öffentlichen Spiele von FV Neuhausen II bei FUSSBALL.DE ab und erzeugt `spiele.ics` neu.

Die FUSSBALL.DE-Spiel-ID wird als stabile Kalender-UID verwendet. Wenn sich Uhrzeit oder Spielort ändern, wird dadurch derselbe Termin aktualisiert statt ein zweiter Termin erzeugt.
