#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
station_exemple.py — le code de la station, version la plus bête qui marche.

    python3 station_exemple.py --sim --scenario depart      # il s'en sort
    python3 station_exemple.py --sim --scenario sable       # il s'écroule

Il lit les mesures radio, décide ce qui se passe au râtelier, tient le
journal de ce qu'il a vu, et le pousse au cloud dès que le réseau le permet.
Deux méthodes à remplir, c'est tout.

Copiez-le en ma_station.py, et attaquez.
"""

import json
import hashlib
import os
import sys
import tempfile
import urllib.request

from korko import Detecteur, lancer, planches_de

# --- Paramétrage de la station et des fichiers de persistance ---
CLOUD = os.environ.get("KORKO_CLOUD", "http://localhost:9000/evenements")
JOURNAL_PATH = os.environ.get(
    "KORKO_JOURNAL",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "station_journal.ndjson"),
)
MAX_REPLAY_PER_ATTEMPT = 20

SEUIL = -80  # dBm : plus faible que ça, on ne compte pas la planche
SILENCE = 10  # secondes sans paquet audible = la planche est partie


# --- État local et logique de détection ---
class Station(Detecteur):

    PERIODE_TIC = 1.0  # tic() toutes les secondes de flux

    def __init__(self):
        self.vues = {}  # balise -> t du dernier paquet au-dessus du seuil
        self.station = "A"
        self.journal = self.charger_journal()
        self.cloud_ok = None  # pour ne signaler que les changements
        self.demarre = False
        print("station_exemple : décisions envoyées à %s" % CLOUD, file=sys.stderr)

    def charger_journal(self):
        """Restaure les événements non acquittés; ignore une dernière ligne tronquée."""
        events = []
        try:
            with open(JOURNAL_PATH, encoding="utf-8") as f:
                for ligne in f:
                    try:
                        ev = json.loads(ligne)
                        if ev.get("evenement") in ("DEPART", "RETOUR", "ETRANGERE"):
                            ev.setdefault("event_id", self.event_id(ev))
                            events.append(ev)
                    except (ValueError, AttributeError):
                        continue
        except FileNotFoundError:
            pass
        except OSError as e:
            print("station_exemple : journal illisible (%s)" % e, file=sys.stderr)
        return events

    @staticmethod
    def event_id(ev):
        champs = [str(ev.get(k, "")) for k in ("station", "evenement", "balise", "t")]
        return hashlib.sha256("\0".join(champs).encode("utf-8")).hexdigest()

    def sauver_journal(self):
        """Réécrit atomiquement: un crash laisse soit l'ancien, soit le nouveau journal."""
        dossier = os.path.dirname(os.path.abspath(JOURNAL_PATH))
        nom_temp = None
        try:
            fd, nom_temp = tempfile.mkstemp(
                prefix=".station-journal-", dir=dossier, text=True
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for ev in self.journal:
                    f.write(
                        json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n"
                    )
                f.flush()
                os.fsync(f.fileno())
            os.replace(nom_temp, JOURNAL_PATH)
            return True
        except OSError as e:
            if nom_temp:
                try:
                    os.unlink(nom_temp)
                except OSError:
                    pass
            print(
                "station_exemple : impossible de sauvegarder le journal (%s)" % e,
                file=sys.stderr,
            )
            return False

    # -- un paquet radio arrive -------------------------------------------
    def observation(self, o):
        self.station = o.station
        if not self.demarre:  # au démarrage, ses planches sont
            self.demarre = True  # supposées au râtelier : celle qui
            for b in planches_de(o.station):  # reste muette sera déclarée partie
                self.vues[b] = o.t
        if o.rssi < SEUIL:  # trop loin : on ignore
            return
        if o.balise not in self.vues:  # on ne la voyait pas : elle rentre
            chez_elle = o.balise in planches_de(o.station)
            self.signaler("RETOUR" if chez_elle else "ETRANGERE", o.balise, o.t)
        self.vues[o.balise] = o.t

    # -- appelée même quand plus rien n'arrive ----------------------------
    def tic(self, t):
        for balise, vue in list(self.vues.items()):
            if t - vue > SILENCE:  # silence prolongé : elle est partie
                del self.vues[balise]
                self.signaler("DEPART", balise, t)
        self.vider()
        if not self.journal:  # les TIC ne gonflent pas le journal
            self.envoyer({"t": t, "station": self.station, "evenement": "TIC"})

    # -- sortie -----------------------------------------------------------
    def signaler(self, type_, balise, t):
        {"DEPART": self.depart, "RETOUR": self.retour, "ETRANGERE": self.etrangere}[
            type_
        ](balise, t, self.station)
        ev = {"t": t, "station": self.station, "balise": balise, "evenement": type_}
        ev["event_id"] = self.event_id(ev)
        self.journal.append(ev)
        self.sauver_journal()
        self.vider()

    # -- si le réseau devenait inaccessible : rien ne se perd -------------
    def vider(self):
        """Envoie le journal dans l'ordre ; s'arrête au premier échec d'envoi."""
        for _ in range(min(len(self.journal), MAX_REPLAY_PER_ATTEMPT)):
            if not self.envoyer(self.journal[0]):
                return False
            acquitte = self.journal.pop(0)
            if not self.sauver_journal():
                # Garder en mémoire et rejouer est préférable à perdre l'événement.
                self.journal.insert(0, acquitte)
                return False
        return True

    def envoyer(self, evenement):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    CLOUD,
                    json.dumps(evenement).encode("utf-8"),
                    {"Content-Type": "application/json"},
                ),
                timeout=0.5,
            ) as response:
                ok = 200 <= response.status < 300
                response.read()
        except Exception:
            ok = False
        if ok != self.cloud_ok:  # on ne prévient qu'au changement
            print(
                "station_exemple : cloud %s"
                % (
                    "joint"
                    if ok
                    else "injoignable, les événements sont gardés au journal"
                ),
                file=sys.stderr,
            )
            self.cloud_ok = ok
        return ok


lancer(Station)


# Ce que cet exemple fait mal, et qui est tout le sujet :
#
#   - un corps mouillé devant la balise fait chuter le RSSI de 20 dB :
#     ici, ça déclenche un faux départ ;
#   - une planche posée sur le sable à six mètres n'est pas partie ;
#   - un seuil sec clignote autour de sa valeur — essayez une hystérésis ;
#   - dix secondes de silence suffisent à le rendre réactif, mais un corps
#     mouillé ou une planche posée un peu loin passent alors pour un départ.
#     Comment rester réactif sans faux départ ?
#   - au démarrage, il suppose toutes ses planches au râtelier : une planche
#     absente est annoncée partie au bout de dix secondes, comme un départ ;
#   - le journal vit en mémoire : si le Pi redémarrait pendant une
#     interruption du réseau, l'historique serait perdu. Un fichier suffirait.
