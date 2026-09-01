"""Adapter für die von SAP auf GitHub veröffentlichte Dokumentation (§8.2, §8.4).

Er liest ein ausgechecktes Repository aus dem `SAP-docs`-Bestand — etwa
`SAP-docs/btp-cloud-platform` mit seinen rund zweitausend Markdown-Dokumenten unter CC-BY-4.0.
Der Zweck ist die Abnahme an **echten Texten**: Ob Clustering brauchbare Themen findet, ob die
Betitelung trifft und ob die Relationserkennung Sinnvolles vorschlägt, lässt sich an
synthetischen Beständen nicht beantworten. Diese Dokumentation liefert echte Fachsprache, echte
Längenverteilung und — der eigentliche Gewinn — eine von Menschen gepflegte Verweisstruktur.

**Warum ein Verzeichnis und kein Netzabruf.** Der Adapter liest lokale Dateien; das Beschaffen
ist ein `git clone` und damit ein Betriebsschritt, kein Codepfad. Das hält ihn offline
lauffähig, macht ihn ohne Netz testbar und passt zu abgeschlossenen Umgebungen (§4.7), in denen
das Repository einmal gespiegelt wird. Ein Adapter, der bei jedem Lauf zweitausend Dateien über
HTTP zöge, gewänne nichts und verlöre all das.

**Die IDs kommen aus der Quelle.** Jedes Dokument trägt in der ersten Zeile eine stabile
SAP-Kennung als Kommentar::

    <!-- loiofa5af4ecdf90496b8eec54fe0e22150c -->

Sie ist genau das, was §22.3 von einer externen ID verlangt: über Läufe stabil und unabhängig
vom Dateinamen, der sich beim Umbenennen eines Kapitels ändert. Ein Dokument ohne diese Kennung
fällt auf seinen Pfad zurück — lieber eine schwächere ID als ein übersprungenes Dokument.

**Die Verweise sind der Grund für den Zwischenindex.** Die Dokumente verlinken einander relativ
(``[Titel](andere-datei-abc1234.md)``, auch über Ordnergrenzen). Um daraus Kanten zu machen,
muss der Zielpfad in dessen Kennung übersetzt werden — deshalb liest ``configure()`` einmal alle
Dateien und merkt sich *nur* Pfad und Kennung. Der Index ist bei zweitausend Dokumenten ein
Bruchteil eines Megabytes; die Texte selbst bleiben ungelesen, bis der Generator sie ausgibt
(§8.2 Regel 1).
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from wissensgraph.config import defaults
from wissensgraph.config.sources import SourceConfig
from wissensgraph.infrastructure.adapters.base import BaseAdapter
from wissensgraph.observability.logging import get_logger
from wissensgraph.ports.sources import (
    AdapterCapabilities,
    Cursor,
    HealthState,
    HealthStatus,
    SourceDocument,
    SourceError,
)

_log = get_logger(__name__)

#: Schlüssel in ``selection`` (§8.4).
SELECTION_DIRECTORY = "directory"
SELECTION_LIMIT = "limit"
SELECTION_FOLDERS = "folders"
SELECTION_EXCLUDE = "exclude"

#: Die Kennung in der ersten Zeile: ``<!-- loio<32 Hexzeichen> -->``.
_LOIO = re.compile(r"<!--\s*loio([0-9a-f]{8,})\s*-->", re.IGNORECASE)

#: Die erste Überschrift erster Ordnung — der Titel des Dokuments.
_TITEL = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

#: Ein Markdown-Link auf ein anderes Dokument des Bestands. Absolute Adressen (``http…``) und
#: Anker (``#…``) sind ausdrücklich nicht gemeint: Das eine verlässt den Bestand, das andere
#: bleibt im selben Dokument — Kanten entstehen weder aus dem einen noch aus dem anderen.
_VERWEIS = re.compile(r"\]\(((?!https?:|#|mailto:)[^)\s]+?\.md)(?:#[^)\s]*)?\)")

#: Eine Markdown-Maskierung: ein Backslash vor einem Sonderzeichen.
_MASKIERT = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>|])")


class SapDocsAdapter(BaseAdapter):
    """Liest ein ausgechecktes `SAP-docs`-Repository als Quelle."""

    name = defaults.ADAPTER_SAP_DOCS
    # Keine Löschungen: Der Bestand ist ein Auschecken zu einem Zeitpunkt. Was in einem späteren
    # Stand fehlt, ist nicht gelöscht, sondern nie mitgekommen — das als Löschung zu melden,
    # würde Tombstones erzeugen (§7.6), die nichts über die Quelle aussagen.
    capabilities = AdapterCapabilities(incremental=True, single_fetch=True, references=True)

    def __init__(self) -> None:
        super().__init__()
        self._wurzel: Path | None = None
        #: Pfad (relativ zur Wurzel) → externe Kennung. Nur für die Auflösung der Verweise.
        self._kennungen: dict[str, str] = {}
        #: Die Dateien in fester Reihenfolge — Läufe sollen vergleichbar bleiben.
        self._dateien: tuple[Path, ...] = ()

    # -- Konfiguration ----------------------------------------------------------

    def configure(self, cfg: SourceConfig) -> None:
        """Baut den Kennungs-Index. Die Texte selbst bleiben ungelesen."""
        super().configure(cfg)
        roh = cfg.selection.get(SELECTION_DIRECTORY)
        if not roh:
            raise SourceError(
                f"Quelle '{cfg.name}': 'selection.directory' fehlt. Der Adapter liest ein "
                f"ausgechecktes SAP-docs-Repository; das Beschaffen ist ein 'git clone' (§8.4)."
            )
        self._wurzel = Path(str(roh))
        self._kennungen = {}
        self._dateien = ()
        if not self._wurzel.is_dir():
            # Kein Startfehler: §8.3 will einen Adapter, der sich selbst abschaltet und in der
            # Oberfläche als unbenutzbar erscheint, statt den ganzen Dienst zu verhindern.
            _log.warning("sapdocs.verzeichnis_fehlt", source=cfg.name, path=str(self._wurzel))
            return

        erlaubt = tuple(str(name) for name in cfg.selection.get(SELECTION_FOLDERS, ()) or ())
        # Muster für Dateien, die keine Wissensdokumente sind. Der Anlass ist gemessen: Das
        # generierte `index.md` des BTP-Bestands verweist auf *jedes* andere Dokument und
        # brachte allein 2.069 der 4.918 Kanten mit. Eine solche Nabe verbindet alles mit allem
        # und macht damit jede Aussage über Nähe wertlos — sie beschreibt die Navigation, nicht
        # den Inhalt.
        ausgeschlossen = tuple(
            str(muster) for muster in cfg.selection.get(SELECTION_EXCLUDE, ()) or ()
        )
        dateien = sorted(
            datei
            for datei in self._wurzel.rglob("*.md")
            if (
                not erlaubt
                or any(teil in erlaubt for teil in datei.relative_to(self._wurzel).parts)
            )
            and not any(datei.relative_to(self._wurzel).match(muster) for muster in ausgeschlossen)
        )
        grenze = cfg.selection.get(SELECTION_LIMIT)
        if isinstance(grenze, int) and grenze > 0:
            dateien = dateien[:grenze]
        self._dateien = tuple(dateien)

        for datei in self._dateien:
            self._kennungen[self._schluessel(datei)] = _kennung_lesen(datei, self._wurzel)
        _log.info(
            "sapdocs.index_gebaut",
            source=cfg.name,
            documents=len(self._dateien),
            directory=str(self._wurzel),
        )

    def _schluessel(self, datei: Path) -> str:
        """Der Pfad relativ zur Wurzel, in einheitlicher Schreibweise."""
        wurzel = self._wurzel
        if wurzel is None:  # pragma: no cover — configure() setzt sie
            raise SourceError(f"Adapter '{self.name}' wurde vor configure() benutzt.")
        return datei.relative_to(wurzel).as_posix()

    # -- Kontrakt ---------------------------------------------------------------

    def health(self) -> HealthStatus:
        """Erreichbar heißt hier: Das Verzeichnis existiert und enthält Dokumente."""
        if self._wurzel is None or not self._wurzel.is_dir():
            return HealthStatus(
                state=HealthState.UNHEALTHY,
                detail=(
                    f"Verzeichnis '{self._wurzel}' gibt es nicht. Erwartet wird ein "
                    f"ausgechecktes SAP-docs-Repository (git clone)."
                ),
            )
        if not self._dateien:
            return HealthStatus(
                state=HealthState.DEGRADED,
                detail=f"'{self._wurzel}' enthält keine Markdown-Dokumente.",
            )
        return HealthStatus(
            state=HealthState.HEALTHY,
            detail=f"{len(self._dateien)} Dokumente unter {self._wurzel}.",
        )

    def iter_documents(self, cursor: Cursor | None) -> Iterator[SourceDocument]:
        """Alle Dokumente des Bestands; mit Cursor nur die seither geänderten."""
        return self._durchreichen(self._lesen(), self.cursor_since(cursor))

    def _lesen(self) -> Iterator[SourceDocument]:
        for datei in self._dateien:
            dokument = self._als_dokument(datei)
            if dokument is not None:
                yield dokument

    def fetch(self, external_id: str) -> SourceDocument | None:
        """Ein einzelnes Dokument über seine Kennung."""
        for datei in self._dateien:
            if self._kennungen.get(self._schluessel(datei)) == external_id:
                return self._als_dokument(datei)
        return None

    # -- Umwandlung -------------------------------------------------------------

    def _als_dokument(self, datei: Path) -> SourceDocument | None:
        """Übersetzt eine Markdown-Datei in das quellneutrale DTO (§8.2)."""
        try:
            roh = datei.read_text(encoding="utf-8")
        except OSError as exc:
            # Eine unlesbare Datei bricht keinen Lauf ab — sie fehlt, und das steht im Protokoll.
            _log.warning("sapdocs.datei_unlesbar", path=str(datei), grund=str(exc))
            return None

        schluessel = self._schluessel(datei)
        titel = _titel_lesen(roh) or datei.stem
        koerper = _koerper(roh)
        verweise = self._verweise(schluessel, roh)
        werte = {
            "external_id": self._kennungen[schluessel],
            "title": titel,
            "description": _anriss(koerper),
            "body": koerper,
            "resource": self._adresse(schluessel),
            # Der Ordner ist das einzige Ordnungsmerkmal, das die Quelle mitbringt — er wird zum
            # Schlagwort und ist damit später der Prüfstein: Findet das Clustering diese
            # Gliederung wieder, ohne sie zu kennen?
            "tags": (datei.relative_to(self._wurzel).parts[0],) if self._wurzel else (),
            "updated_at": datetime.fromtimestamp(datei.stat().st_mtime, tz=UTC),
            "references": verweise,
            "extra": {"path": schluessel},
        }
        werte.update(self.mapping.apply(werte))
        return SourceDocument.model_validate(werte)

    def _verweise(self, schluessel: str, roh: str) -> tuple[str, ...]:
        """Löst die relativen Markdown-Links in externe Kennungen auf (§8.5).

        Ein Verweis, dessen Ziel nicht im Bestand liegt, wird stillschweigend übergangen: Er
        zeigt aus dem ausgecheckten Ausschnitt heraus, und eine Kante auf ein Konzept, das es
        hier nie geben wird, wäre eine Behauptung über etwas Abwesendes.
        """
        ordner = PurePosixPath(schluessel).parent
        gefunden: list[str] = []
        for treffer in _VERWEIS.finditer(roh):
            # `posixpath.normpath` und nicht `Path.resolve`: Der Verweis wird *innerhalb* des
            # Bestands aufgelöst, nicht gegen das Dateisystem. `resolve()` bezöge '..' auf das
            # Arbeitsverzeichnis des Prozesses und fände ordnerübergreifende Verweise nie —
            # und genau die sind die interessanten.
            ziel = posixpath.normpath(str(ordner / treffer.group(1)))
            kennung = self._kennungen.get(ziel)
            if kennung is not None and kennung not in gefunden:
                gefunden.append(kennung)
        return tuple(gefunden)

    def _adresse(self, schluessel: str) -> str | None:
        """Die Web-Adresse des Dokuments — die Quelle, aus der es stammt."""
        basis = self.config.connection.web_base_url or self.config.connection.base_url
        if not basis:
            return None
        return f"{basis.rstrip('/')}/{schluessel}"


def _kennung_lesen(datei: Path, wurzel: Path) -> str:
    """Die stabile SAP-Kennung aus der ersten Zeile; ersatzweise der Pfad.

    Der Rückfall ist Absicht: Ein Dokument ohne Kennung soll mitkommen. Sein Pfad ist eine
    schwächere ID — er ändert sich beim Umbenennen —, aber eine ausgelassene Seite wäre
    schlimmer als eine, die nach einer Umbenennung einmal neu entsteht.
    """
    try:
        with datei.open(encoding="utf-8") as strom:
            for _ in range(5):
                zeile = strom.readline()
                if not zeile:
                    break
                treffer = _LOIO.search(zeile)
                if treffer is not None:
                    return treffer.group(1).lower()
    except OSError:
        pass
    return datei.relative_to(wurzel).as_posix()


def _titel_lesen(roh: str) -> str | None:
    """Die erste Überschrift erster Ordnung, ohne Markdown-Maskierung."""
    treffer = _TITEL.search(roh)
    return None if treffer is None else _entmaskieren(treffer.group(1).strip())


def _entmaskieren(text: str) -> str:
    """Nimmt die Markdown-Maskierung heraus: ``Header Data \\(v2\\)`` → ``Header Data (v2)``.

    SAP maskiert Klammern und Unterstriche im Fließtext, weil sie dort Auszeichnung wären. In
    einem Titel sind sie es nicht — dort ist die Maskierung nur ein Rückstand des Formats, der
    sonst in der Oberfläche steht und mit ins Embedding geht.
    """
    return _MASKIERT.sub(r"\1", text)


def _koerper(roh: str) -> str:
    """Der Text ohne den Kennungs-Kommentar und ohne führende Leerzeilen."""
    ohne_kennung = _LOIO.sub("", roh, count=1)
    return ohne_kennung.strip()


def _anriss(koerper: str, grenze: int = 400) -> str | None:
    """Der erste echte Absatz als Beschreibung.

    Er ist das, was in Listen und in der Kernspace-Übersicht steht — und das, was das
    Embedding zuerst sieht. Überschriften und Bildzeilen sind dafür wertlos, deshalb wird der
    erste Absatz gesucht, der wie ein Satz aussieht.
    """
    for absatz in koerper.split("\n\n"):
        text = _entmaskieren(" ".join(absatz.split()))
        if not text or text.startswith(("#", "|", ">", "-", "*", "!", "<")):
            continue
        return text if len(text) <= grenze else f"{text[: grenze - 1].rstrip()}…"
    return None
