"""Der Einrichtungsassistent: alle Einstellungen an einer Stelle abfragbar (§6).

Die Konfiguration dieses Systems ist bewusst breit — sie soll ohne Codeänderung tragen, was
eine Installation unterscheidet (§6.1 Regel 1). Der Preis dafür sind rund achtzig Werte in drei
Dateien, verteilt auf zwei Wege: ``config/*.yaml`` für alles Fachliche, ``.env`` für alles, was
Zugang, Adresse oder Geheimnis ist (§20.2). Wer das System zum ersten Mal aufsetzt, muss heute
beide Dateien lesen und wissen, welcher Wert wohin gehört.

Dieses Modul beantwortet die Frage stattdessen. Es baut einen **Katalog** aller einstellbaren
Werte und kann ihn lesen, prüfen und zurückschreiben. Die Kommandozeile darum herum steht in
:mod:`wissensgraph.cli` (``wg setup``); hier ist nichts, was einen Bildschirm braucht — und
genau deshalb ist es prüfbar.

**Der Katalog wird abgeleitet, nicht gepflegt.** Eine zweite Liste aller Einstellungen wäre
binnen eines Sprints falsch: Wer ein Feld hinzufügt, denkt nicht an sie. Die drei Quellen sind
deshalb die, die es ohnehin gibt:

1. :data:`~wissensgraph.config.env_mapping.ENV_BINDINGS` — die dokumentierte ENV-Schnittstelle
   (§6.4) mit Zielpfad und Beschreibung.
2. Die ``${WG_...}``-Platzhalter in den YAML-Dateien. Sie sagen für jeden Wert, ob er über die
   Umgebung gesetzt wird — und mit welchem Rückfallwert.
3. Das Pydantic-Schema :class:`~wissensgraph.config.schema.Settings` mit Typ, Vorgabe,
   Wertebereich und ``description``.

Daraus folgt die Regel, nach der der Assistent entscheidet, *wohin* ein Wert geschrieben wird,
und sie ist keine Konvention, sondern eine Ablesung:

> Steht im YAML an dieser Stelle ein Platzhalter, gehört der Wert in ``.env``.
> Steht dort ein Literal, gehört er ins YAML.

``.env.example`` kommt als vierte Quelle hinzu, aber nur für die *Prosa*: Es trägt die
Erklärungen zu den Variablen, die kein Schema kennt — die Herkunft der Images, den Proxy, die
Ports der Container. Der Assistent liest sie von dort und erfindet keine eigenen.

**Geschrieben wird zeilenweise und nicht durch Neuerzeugen.** Beide Dateien bestehen zum
größeren Teil aus Kommentaren, und die sind der eigentliche Wert: In ``config/wissensgraph.yaml``
stehen neben ``min_similarity`` fünfundzwanzig Zeilen, die erklären, warum 0,80 dort steht und
was ein anderer Wert bewirkt. Ein Assistent, der die Datei aus einem Dictionary neu schriebe,
löschte das — und niemand bemerkte es, bis jemand die Zahl das nächste Mal ändern will.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from wissensgraph.config import defaults
from wissensgraph.config.env_mapping import ENV_BINDINGS
from wissensgraph.config.schema import Settings

#: Ein Platzhalter in einer YAML-Datei: ``${NAME}`` oder ``${NAME:-rückfall}``.
PLATZHALTER = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<rueckfall>[^}]*))?\}")

#: Woran ein Wert als Geheimnis erkannt wird. Absichtlich großzügig: Ein fälschlich maskierter
#: Wert kostet einen Tastendruck, ein fälschlich angezeigter steht im Terminalprotokoll und in
#: der Bildschirmaufnahme (§21.1).
GEHEIM_MUSTER = re.compile(r"(TOKEN|KEY|PASSWORD|SECRET|CREDENTIAL|DSN)", re.IGNORECASE)

#: Wie ein maskierter Wert erscheint. Bewusst ASCII und dieselbe Maske, die auch Logs und
#: ``/api/v1/config/effective`` benutzen (§20.2): Eine Windows-Konsole in einer Codepage ohne
#: Unicode bricht bei Blockzeichen ab, und ein Einrichtungswerkzeug, das an seiner eigenen
#: Ausgabe scheitert, ist wertlos.
MASKE = defaults.SECRET_MASK


def ist_geheim(name: str) -> bool:
    """Ob ein Wert dieses Namens nie im Klartext angezeigt werden darf (§21.1)."""
    return bool(GEHEIM_MUSTER.search(name))


def maskiert(name: str, wert: str) -> str:
    """Der anzeigbare Wert: Geheimnisse nur als Länge, alles andere im Klartext."""
    if not wert:
        return ""
    if not ist_geheim(name):
        return wert
    return f"{MASKE} ({len(wert)} Zeichen)"


# -- Der Katalog ----------------------------------------------------------------


@dataclass(frozen=True)
class Eintrag:
    """Eine einstellbare Größe — unabhängig davon, in welcher Datei sie am Ende landet."""

    schluessel: str
    """Wie der Wert angesprochen wird: der ENV-Name, oder der Punktpfad im YAML."""

    abschnitt: str
    """Überschrift für die Anzeige — die Gruppierung, in der jemand sucht."""

    beschreibung: str
    """Wozu der Wert dient. Aus dem Schema, der ENV-Tabelle oder ``.env.example``."""

    ziel: Literal["env", "yaml"]
    """Wohin geschrieben wird. Abgelesen am Platzhalter, nicht festgelegt."""

    pfad: tuple[str, ...] = ()
    """Der Pfad in der Konfigurationsstruktur; leer bei reinen Umgebungsvariablen."""

    vorgabe: str = ""
    """Was gilt, wenn nichts gesetzt ist."""

    auswahl: tuple[str, ...] = ()
    """Die erlaubten Werte, wenn es eine geschlossene Menge ist (``Literal`` im Schema)."""

    typ: str = "text"
    """``text``, ``ganzzahl``, ``kommazahl``, ``wahrheitswert`` oder ``liste``."""

    pflicht: bool = False
    """Ob das System ohne diesen Wert nicht startet."""

    @property
    def geheim(self) -> bool:
        """Ob der Wert maskiert angezeigt werden muss."""
        return ist_geheim(self.schluessel)

    def pruefen(self, wert: str) -> str | None:
        """Prüft eine Eingabe gegen Typ und Auswahl; ``None`` heißt in Ordnung.

        Bewusst nur die Prüfungen, die *diese eine* Eingabe betreffen. Alles Übergreifende —
        ob ein Scope auf einen Store zeigt, den es gibt, ob die Embedding-Dimension zum Modell
        passt — prüft das Schema am Ende in einem Rutsch (:func:`pruefe_gesamt`). Zwei
        Prüfstellen mit derselben Aufgabe drifteten auseinander.
        """
        if not wert:
            return "Ein Pflichtwert darf nicht leer bleiben." if self.pflicht else None
        if self.auswahl and wert not in self.auswahl:
            return f"Erlaubt sind: {', '.join(self.auswahl)}."
        if self.typ == "ganzzahl":
            try:
                int(wert)
            except ValueError:
                return "Erwartet wird eine ganze Zahl."
        if self.typ == "kommazahl":
            try:
                float(wert)
            except ValueError:
                return "Erwartet wird eine Zahl."
        if self.typ == "wahrheitswert" and wert.lower() not in {
            "true",
            "false",
            "1",
            "0",
            "yes",
            "no",
            "on",
            "off",
        }:
            return "Erwartet wird true oder false."
        return None


@dataclass
class Katalog:
    """Alle einstellbaren Werte, gruppiert und in der Reihenfolge der Dateien."""

    eintraege: tuple[Eintrag, ...] = ()

    def __iter__(self) -> Iterator[Eintrag]:
        return iter(self.eintraege)

    def __len__(self) -> int:
        return len(self.eintraege)

    @property
    def abschnitte(self) -> tuple[str, ...]:
        """Die Abschnitte in ihrer Reihenfolge, ohne Wiederholung."""
        gesehen: list[str] = []
        for eintrag in self.eintraege:
            if eintrag.abschnitt not in gesehen:
                gesehen.append(eintrag.abschnitt)
        return tuple(gesehen)

    def im_abschnitt(self, name: str) -> tuple[Eintrag, ...]:
        """Die Einträge eines Abschnitts."""
        return tuple(item for item in self.eintraege if item.abschnitt == name)

    def get(self, schluessel: str) -> Eintrag | None:
        """Der Eintrag zu einem Schlüssel, oder ``None``."""
        for eintrag in self.eintraege:
            if eintrag.schluessel == schluessel:
                return eintrag
        return None


# -- Aufbau des Katalogs --------------------------------------------------------


@dataclass(frozen=True)
class _EnvZeile:
    """Eine Variable aus ``.env.example`` mit dem Kommentar, der über ihr steht."""

    name: str
    vorgabe: str
    abschnitt: str
    beschreibung: str


_ABSCHNITT = re.compile(r"^#\s*-{2,}\s*(?P<titel>.+?)\s*-{2,}\s*$")
_ZUWEISUNG = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<wert>.*)$")


def lies_env_beispiel(pfad: Path) -> tuple[_EnvZeile, ...]:
    """Liest ``.env.example`` als Katalogquelle: Abschnitte, Erklärungen, Vorgaben.

    Die Datei ist ohnehin die Anleitung für Menschen; sie hier ein zweites Mal in Python zu
    beschreiben hieße, zwei Wahrheiten zu pflegen. Kommentarzeilen unmittelbar über einer
    Zuweisung gelten als deren Erklärung — genauso, wie ein Leser sie versteht.
    """
    if not pfad.is_file():
        return ()

    zeilen: list[_EnvZeile] = []
    abschnitt = "Allgemein"
    sammlung: list[str] = []
    for roh in pfad.read_text(encoding="utf-8").splitlines():
        zeile = roh.rstrip()
        kopf = _ABSCHNITT.match(zeile)
        if kopf is not None:
            abschnitt = kopf.group("titel").strip()
            sammlung = []
            continue
        if zeile.startswith("#"):
            sammlung.append(zeile.lstrip("#").strip())
            continue
        if not zeile:
            # Eine Leerzeile trennt: Was davor stand, gehört nicht mehr zur nächsten Variablen.
            sammlung = []
            continue
        treffer = _ZUWEISUNG.match(zeile)
        if treffer is None:
            continue
        zeilen.append(
            _EnvZeile(
                name=treffer.group("name"),
                vorgabe=treffer.group("wert").strip(),
                abschnitt=abschnitt,
                beschreibung=" ".join(teil for teil in sammlung if teil),
            )
        )
        sammlung = []
    return tuple(zeilen)


def platzhalter_von(text: str) -> dict[str, str | None]:
    """Alle ``${WG_...}``-Platzhalter eines YAML-Textes mit ihrem Rückfallwert.

    ``None`` heißt: **kein** Rückfallwert. Der Unterschied zu einer leeren Zeichenkette ist der
    zwischen Pflicht und Kür — ``${WG_EMBEDDING_DIM}`` verhindert den Start, solange nichts
    gesetzt ist (§6.1 Regel 3), ``${WG_BROKER_URL:-}`` sagt ausdrücklich "leer ist in Ordnung".
    Beides auf "" abzubilden machte aus jeder optionalen Angabe eine Pflicht.
    """
    gefunden: dict[str, str | None] = {}
    for zeile in text.splitlines():
        # Auskommentierte Zeilen zählen nicht. In ``sources.yaml`` steht der Gateway-Schlüssel
        # als Beispiel hinter einem '#' — als Platzhalter gelesen machte der Assistent daraus
        # eine Pflichtangabe für eine Kopfzeile, die niemand eingeschaltet hat.
        if zeile.lstrip().startswith("#"):
            continue
        for treffer in PLATZHALTER.finditer(zeile):
            name = treffer.group("name")
            if name not in gefunden:
                gefunden[name] = treffer.group("rueckfall")
    return gefunden


def _typname(annotation: Any) -> str:
    """Der Typ eines Schemafeldes als Wort, das in einer Eingabeaufforderung stehen kann."""
    if annotation is bool:
        return "wahrheitswert"
    if annotation is int:
        return "ganzzahl"
    if annotation is float:
        return "kommazahl"
    ursprung = get_origin(annotation)
    if ursprung in (Union, UnionType):
        for teil in get_args(annotation):
            if teil is not type(None):
                return _typname(teil)
    if ursprung in (tuple, list):
        return "liste"
    return "text"


def _auswahl(annotation: Any) -> tuple[str, ...]:
    """Die erlaubten Werte eines ``Literal``-Feldes, sonst leer."""
    if get_origin(annotation) is Literal:
        return tuple(str(wert) for wert in get_args(annotation))
    ursprung = get_origin(annotation)
    if ursprung in (Union, UnionType):
        for teil in get_args(annotation):
            treffer = _auswahl(teil)
            if treffer:
                return treffer
    return ()


def _ist_modell(annotation: Any) -> type[BaseModel] | None:
    """Das verschachtelte Modell eines Feldes, falls es eines ist."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _vorgabe_als_text(info: FieldInfo) -> tuple[str, bool]:
    """Die Vorgabe eines Feldes als Zeichenkette, plus ob es ein Pflichtfeld ist."""
    if info.is_required():
        return "", True
    wert = info.get_default(call_default_factory=True)
    if wert is None:
        return "", False
    if isinstance(wert, bool):
        return ("true" if wert else "false"), False
    if isinstance(wert, (tuple, list)):
        return ",".join(str(teil) for teil in wert), False
    if isinstance(wert, BaseModel):
        return "", False
    return str(wert), False


def _schemafelder(
    modell: type[BaseModel], praefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], FieldInfo]]:
    """Alle *skalaren* Felder eines Schemas, mit ihrem Pfad — verschachtelt aufgelöst.

    Sammlungen ohne feste Gestalt (``stores``, ``scopes``, ``concept_types``) bleiben außen vor.
    Sie sind keine Einstellung, die man abfragt, sondern eine Struktur, die man entwirft: Wie
    viele Scopes es gibt und wie sie heißen, ist eine Entscheidung über den Zuschnitt der
    Installation und keine, die in eine Eingabezeile passt. Der Assistent zeigt sie an und
    verweist auf die Datei.
    """
    for name, info in modell.model_fields.items():
        pfad = (*praefix, name)
        verschachtelt = _ist_modell(info.annotation)
        if verschachtelt is not None:
            yield from _schemafelder(verschachtelt, pfad)
            continue
        ursprung = get_origin(info.annotation)
        if ursprung is dict:
            continue
        if ursprung in (tuple, list) and any(
            _ist_modell(teil) is not None for teil in get_args(info.annotation)
        ):
            continue
        yield pfad, info


def baue_katalog(
    *,
    env_beispiel: Path,
    config_datei: Path,
    weitere_yaml: Sequence[Path] = (),
) -> Katalog:
    """Stellt den vollständigen Katalog aus Schema, Platzhaltern und ``.env.example`` zusammen.

    Die Reihenfolge folgt ``.env.example`` und danach dem Schema — also der Reihenfolge, in der
    ein Mensch die Dateien liest. Ein Wert erscheint genau einmal: Steht im YAML ein
    Platzhalter, ist er eine Umgebungsvariable und keine YAML-Zeile.

    Args:
        env_beispiel: Die Vorlage ``.env.example`` — Quelle der Abschnitte und Erklärungen.
        config_datei: ``config/wissensgraph.yaml`` — Quelle der Platzhalter und der YAML-Werte.
        weitere_yaml: ``models.yaml``, ``sources.yaml`` — nur für ihre Platzhalter.
    """
    yaml_text = config_datei.read_text(encoding="utf-8") if config_datei.is_file() else ""
    platzhalter = platzhalter_von(yaml_text)
    for datei in weitere_yaml:
        if datei.is_file():
            for name, rueckfall in platzhalter_von(datei.read_text(encoding="utf-8")).items():
                platzhalter.setdefault(name, rueckfall)

    bindungen = {binding.variable: binding for binding in ENV_BINDINGS}
    schema = dict(_schemafelder(Settings))
    # Über welchen ENV-Namen ein Schemafeld gesetzt wird — die Umkehrung der Bindungstabelle.
    ueber_env = {binding.path: binding.variable for binding in ENV_BINDINGS}

    eintraege: list[Eintrag] = []
    gesehen: set[str] = set()

    # 1. Die Umgebungsvariablen, in der Reihenfolge und mit den Erklärungen der Vorlage.
    for zeile in lies_env_beispiel(env_beispiel):
        if zeile.name in gesehen:
            continue
        gesehen.add(zeile.name)
        bindung = bindungen.get(zeile.name)
        pfad = bindung.path if bindung is not None else ()
        info = schema.get(pfad) if pfad else None
        beschreibung = zeile.beschreibung or (bindung.description if bindung else "")
        if not beschreibung and info is not None and info.description:
            beschreibung = info.description
        eintraege.append(
            Eintrag(
                schluessel=zeile.name,
                abschnitt=zeile.abschnitt,
                beschreibung=beschreibung,
                ziel="env",
                pfad=pfad,
                vorgabe=zeile.vorgabe or (platzhalter.get(zeile.name) or ""),
                auswahl=_auswahl(info.annotation) if info is not None else (),
                typ=_typname(info.annotation) if info is not None else "text",
                pflicht=platzhalter.get(zeile.name, "") is None,
            )
        )

    # 2. Platzhalter, die in einer YAML-Datei stehen, aber in der Vorlage fehlen. Sie wären
    #    sonst unsichtbar — und ein Platzhalter ohne Rückfallwert verhindert den Start (§6.1
    #    Regel 3). Genau die will ein Assistent zeigen.
    for name, rueckfall in platzhalter.items():
        if name in gesehen:
            continue
        gesehen.add(name)
        bindung = bindungen.get(name)
        pfad = bindung.path if bindung is not None else ()
        info = schema.get(pfad) if pfad else None
        eintraege.append(
            Eintrag(
                schluessel=name,
                abschnitt="Weitere Umgebungsvariablen",
                beschreibung=(
                    bindung.description
                    if bindung is not None
                    else (info.description if info is not None and info.description else "")
                ),
                ziel="env",
                pfad=pfad,
                vorgabe=rueckfall or "",
                auswahl=_auswahl(info.annotation) if info is not None else (),
                typ=_typname(info.annotation) if info is not None else "text",
                pflicht=rueckfall is None,
            )
        )

    # 3. Die fachlichen Werte: alles im Schema, was nicht schon über die Umgebung läuft.
    for pfad, info in schema.items():
        env_name = ueber_env.get(pfad)
        if env_name is not None and env_name in gesehen:
            continue
        if _yaml_ist_platzhalter(yaml_text, pfad):
            continue
        vorgabe, pflicht = _vorgabe_als_text(info)
        eintraege.append(
            Eintrag(
                schluessel=".".join(pfad),
                abschnitt=f"config/wissensgraph.yaml — {pfad[0]}",
                beschreibung=info.description or "",
                ziel="yaml",
                pfad=pfad,
                vorgabe=vorgabe,
                auswahl=_auswahl(info.annotation),
                typ=_typname(info.annotation),
                pflicht=pflicht,
            )
        )

    return Katalog(tuple(eintraege))


def _yaml_ist_platzhalter(text: str, pfad: tuple[str, ...]) -> bool:
    """Ob im YAML an diesem Pfad ein ``${...}`` steht — dann gehört der Wert in die Umgebung."""
    zeile = finde_yaml_zeile(text.splitlines(), pfad)
    if zeile is None:
        return False
    _, inhalt = zeile
    return bool(PLATZHALTER.search(inhalt))


# -- Lesen und Schreiben --------------------------------------------------------


def lies_env(pfad: Path) -> dict[str, str]:
    """Die gesetzten Werte einer ``.env``-Datei; Kommentare und Leerzeilen entfallen."""
    if not pfad.is_file():
        return {}
    werte: dict[str, str] = {}
    for roh in pfad.read_text(encoding="utf-8").splitlines():
        zeile = roh.strip()
        if not zeile or zeile.startswith("#"):
            continue
        treffer = _ZUWEISUNG.match(zeile)
        if treffer is not None:
            werte[treffer.group("name")] = treffer.group("wert").strip()
    return werte


def schreibe_env(pfad: Path, aenderungen: Mapping[str, str], *, vorlage: Path | None = None) -> int:
    """Schreibt Werte in eine ``.env``-Datei und gibt zurück, wie viele sich geändert haben.

    Bestehende Zeilen werden an Ort und Stelle ersetzt, alles andere bleibt stehen — auch die
    Kommentare, und die sind hier der halbe Inhalt. Ein unbekannter Schlüssel kommt ans Ende,
    unter eine Überschrift, die ihn als Zugabe des Assistenten ausweist.

    Gibt es die Datei noch nicht, wird sie aus ``vorlage`` erzeugt. So beginnt eine frische
    Installation nicht mit einer nackten Liste, sondern mit derselben Anleitung, die im
    Repository liegt.
    """
    if not pfad.is_file() and vorlage is not None and vorlage.is_file():
        pfad.write_text(vorlage.read_text(encoding="utf-8"), encoding="utf-8")

    zeilen = pfad.read_text(encoding="utf-8").splitlines() if pfad.is_file() else []
    offen = dict(aenderungen)
    geaendert = 0

    for index, roh in enumerate(zeilen):
        treffer = _ZUWEISUNG.match(roh.strip())
        if treffer is None:
            continue
        name = treffer.group("name")
        if name not in offen:
            continue
        neu = offen.pop(name)
        if treffer.group("wert").strip() != neu:
            zeilen[index] = f"{name}={neu}"
            geaendert += 1

    if offen:
        zeilen.append("")
        zeilen.append("# --- Vom Einrichtungsassistenten ergänzt ---------------------------")
        for name in sorted(offen):
            zeilen.append(f"{name}={offen[name]}")
            geaendert += 1

    pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return geaendert


def finde_yaml_zeile(zeilen: Sequence[str], pfad: tuple[str, ...]) -> tuple[int, str] | None:
    """Sucht die Zeile eines YAML-Pfades — über die Einrückung, ohne YAML zu parsen.

    Ein Parser wäre einfacher zu schreiben und für diesen Zweck falsch: Er gäbe eine
    Datenstruktur zurück, und wer sie zurückschreibt, verliert jeden Kommentar. Gesucht wird
    deshalb die *Zeile*, damit nur sie sich ändert.
    """
    tiefe = 0
    gesuchte_einrueckung = 0
    for index, roh in enumerate(zeilen):
        if not roh.strip() or roh.lstrip().startswith("#"):
            continue
        einrueckung = len(roh) - len(roh.lstrip())
        if einrueckung < gesuchte_einrueckung and tiefe > 0:
            # Der Block, in dem gesucht wurde, ist zu Ende — der Pfad existiert nicht.
            return None
        if einrueckung != gesuchte_einrueckung:
            continue
        name, trenner, rest = roh.strip().partition(":")
        if not trenner or name.strip() != pfad[tiefe]:
            continue
        if tiefe == len(pfad) - 1:
            return index, rest.strip()
        tiefe += 1
        gesuchte_einrueckung = einrueckung + 2
    return None


def _blockende(zeilen: Sequence[str], start: int, einrueckung: int) -> int:
    """Der Index nach der letzten Zeile, die noch zum Block ab ``start`` gehört."""
    ende = start + 1
    for index in range(start + 1, len(zeilen)):
        roh = zeilen[index]
        if not roh.strip():
            continue
        if len(roh) - len(roh.lstrip()) <= einrueckung:
            return ende
        ende = index + 1
    return ende


def _einfuegen(zeilen: list[str], pfad: tuple[str, ...], wert: str) -> bool:
    """Legt einen Wert an, den es in der Datei noch nicht gibt. ``False``, wenn das nicht geht.

    Nötig, weil ``config/wissensgraph.yaml`` nicht jedes Feld des Schemas nennt: Was seine
    Vorgabe behält, steht dort gar nicht. ``search`` etwa fehlt vollständig — der Assistent
    könnte sonst keinen einzigen Suchparameter setzen, und das wäre kein Assistent für *alle*
    Einstellungen.

    Eingefügt wird so tief, wie der Pfad schon existiert: Gibt es ``search:`` bereits, kommt der
    Wert als letzte Zeile in diesen Block; gibt es ihn nicht, entsteht am Dateiende ein neuer.
    Der Vermerk daneben ist Absicht — jeder andere Wert in dieser Datei trägt eine Begründung,
    und eine Zeile ohne wäre sonst nicht einzuordnen.
    """
    tiefe = 0
    einfuegepunkt = len(zeilen)
    einrueckung = 0
    gesucht = 0
    for index, roh in enumerate(zeilen):
        if not roh.strip() or roh.lstrip().startswith("#"):
            continue
        aktuell = len(roh) - len(roh.lstrip())
        if aktuell != gesucht:
            continue
        name, trenner, inhalt = roh.strip().partition(":")
        if not trenner or name.strip() != pfad[tiefe]:
            continue
        tiefe += 1
        if tiefe == len(pfad):
            return False  # Gibt es doch — dann hätte finde_yaml_zeile es gefunden.
        if inhalt.strip():
            # Der Pfad führt durch einen Wert hindurch. Etwas darunter einzurücken ergäbe kein
            # gültiges YAML mehr — und eine kaputte Konfigurationsdatei ist der eine Schaden,
            # den ein Einrichtungswerkzeug niemals anrichten darf.
            return False
        einfuegepunkt = _blockende(zeilen, index, aktuell)
        einrueckung = aktuell + 2
        gesucht = einrueckung

    rest = pfad[tiefe:]
    neu: list[str] = []
    if tiefe == 0:
        neu.append("")
        neu.append("# Vom Einrichtungsassistenten ergänzt ('wg setup').")
    for stufe, name in enumerate(rest[:-1]):
        neu.append(" " * (einrueckung + stufe * 2) + f"{name}:")
    neu.append(" " * (einrueckung + (len(rest) - 1) * 2) + f"{rest[-1]}: {wert}")
    zeilen[einfuegepunkt:einfuegepunkt] = neu
    return True


def schreibe_yaml(pfad: Path, aenderungen: Mapping[tuple[str, ...], str]) -> tuple[int, list[str]]:
    """Ersetzt Werte in einer YAML-Datei zeilenweise und meldet, was nicht ging.

    Returns:
        Wie viele Zeilen geändert wurden, und die Pfade, die nicht gefunden wurden. Ein nicht
        gefundener Pfad wird **nicht** angehängt: In einer Datei mit Kommentaren wäre eine
        angehängte Zeile ohne Zusammenhang schlimmer als ein Hinweis an den Aufrufer.
    """
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    geaendert = 0
    fehlend: list[str] = []

    for zielpfad, wert in aenderungen.items():
        gefunden = finde_yaml_zeile(zeilen, zielpfad)
        if gefunden is None:
            if _einfuegen(zeilen, zielpfad, wert):
                geaendert += 1
            else:
                fehlend.append(".".join(zielpfad))
            continue
        index, alt = gefunden
        if alt == wert:
            continue
        roh = zeilen[index]
        einrueckung = roh[: len(roh) - len(roh.lstrip())]
        zeilen[index] = f"{einrueckung}{zielpfad[-1]}: {wert}"
        geaendert += 1

    pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return geaendert, fehlend


# -- Prüfen ---------------------------------------------------------------------


@dataclass
class Befund:
    """Das Ergebnis einer Prüfung — was fehlt, was widerspricht sich."""

    fehler: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)

    @property
    def in_ordnung(self) -> bool:
        """Ob die Konfiguration in diesem Zustand starten würde."""
        return not self.fehler


def pruefe_gesamt(*, config_datei: Path, env: Mapping[str, str]) -> Befund:
    """Baut die Einstellungen genau so, wie es der Start täte, und meldet, was scheitert.

    Der Assistent prüft nicht selbst — er ruft den Lader auf. Eine zweite Prüfung neben §6.5
    wäre eine zweite Meinung darüber, was gültig ist, und die falsche von beiden bemerkte
    niemand, bis der Dienst nicht startet.
    """
    from wissensgraph.config.errors import ConfigError
    from wissensgraph.config.loader import build_settings

    befund = Befund()
    try:
        build_settings(config_file=config_datei, env=dict(env))
    except ConfigError as exc:
        befund.fehler.append(str(exc))
    except Exception as exc:
        befund.fehler.append(f"{type(exc).__name__}: {exc}")

    for name, wert in sorted(env.items()):
        if name.endswith("__API_KEY") and not wert:
            befund.hinweise.append(
                f"{name} ist leer — der zugehörige Anbieter bleibt ohne Zugang. "
                f"Ohne Modell läuft der Lesepfad weiter, die semantische Schicht nicht (§11.5)."
            )
    if env.get("WG_API_AUTH_MODE") == "token" and not env.get("WG_API_TOKEN", "").strip():
        befund.fehler.append(
            "WG_API_AUTH_MODE=token, aber WG_API_TOKEN ist leer. Die API startet so nicht (§20.3)."
        )
    if env.get("WG_API_TOKEN", "").strip() == defaults.API_TOKEN_PLATZHALTER:
        befund.fehler.append(
            f"WG_API_TOKEN steht noch auf '{defaults.API_TOKEN_PLATZHALTER}'. Das ist kein "
            f"Geheimnis, sondern eine Erinnerung daran, eines zu setzen."
        )
    return befund


__all__ = [
    "MASKE",
    "Befund",
    "Eintrag",
    "Katalog",
    "baue_katalog",
    "finde_yaml_zeile",
    "ist_geheim",
    "lies_env",
    "maskiert",
    "platzhalter_von",
    "pruefe_gesamt",
    "schreibe_env",
    "schreibe_yaml",
]
