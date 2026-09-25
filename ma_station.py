#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ma_station.py — détection radio, journal durable et API client hors ligne.

    python3 ma_station.py --sim --scenario depart      # il s'en sort
    python3 ma_station.py --sim --scenario sable       # il s'écroule

Il lit les mesures radio, décide ce qui se passe au râtelier, tient le
journal de ce qu'il a vu, et le pousse au cloud dès que le réseau le permet.
La détection reste fondée sur un seuil radio et une durée de silence.
Le journal et l’état local permettent de reprendre après un redémarrage.
"""

import json
import os
import sys
import time
import urllib.request
import hashlib
import hmac
import base64
import time
import uuid
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

JOURNAL_PATH = os.environ.get(
    "KORKO_STATION_JOURNAL",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "station_journal.ndjson"),
)
STATE_PATH = os.environ.get(
    "KORKO_STATION_STATE",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "station_offline_state.json"
    ),
)

from korko import Detecteur, lancer, planches_de

# --- Configuration de la station et de l'API hors ligne ---
CLOUD = os.environ.get("KORKO_CLOUD", "http://localhost:9000/evenements")
LOCAL_PORT = int(os.environ.get("KORKO_OFFLINE_PORT", "9100"))
OFFLINE_SECRET = os.environ.get(
    "KORKO_OFFLINE_SECRET", "korko-hackathon-offline-secret"
)
HOME_BOARDS = {
    "A": ["korko-01", "korko-02"],
    "B": ["korko-03", "korko-04"],
    "C": ["korko-05", "korko-06"],
}
lock = threading.RLock()

SEUIL = -80  # dBm : plus faible que ça, on ne compte pas la planche
SILENCE = 10  # secondes sans paquet audible = la planche est partie


# --- État local et logique de détection ---
class Station(Detecteur):
    """Détecte les mouvements et conserve les réservations pendant une coupure."""

    PERIODE_TIC = 1.0  # tic() toutes les secondes de flux

    def __init__(self):
        self.vues = {}  # balise -> t du dernier paquet au-dessus du seuil
        self.station = "A"
        self.journal = []  # événements que le cloud n'a pas encore reçus
        try:
            with open(JOURNAL_PATH, encoding="utf-8") as f:
                self.journal = [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            pass
        self.cloud_ok = None  # pour ne signaler que les changements
        self.demarre = False
        self.offline_reservations = {}
        self.maintenance = set()
        self.local_states = {}
        self.current_t = 0.0
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            self.local_states.update(saved.get("local_states", {}))
            self.maintenance.update(saved.get("maintenance", []))
            self.offline_reservations.update(saved.get("offline_reservations", {}))
        except FileNotFoundError:
            pass
        # Rejouer les événements non acquittés dans leur ordre initial : la
        # réservation doit précéder le départ et le retour de sa planche.
        for ev in self.journal:
            if ev.get("evenement") == "OFFLINE_RESERVATION":
                self.offline_reservations[ev["offline_reservation_id"]] = ev
                self.local_states[ev.get("balise")] = "réservée"
            elif ev.get("evenement") == "DEPART":
                self.local_states[ev.get("balise")] = "en mer"
                for reservation in self.offline_reservations.values():
                    if reservation.get("balise") == ev.get("balise"):
                        reservation["status"] = "EN_COURS"
                        reservation["depart_t"] = ev.get("t")
            elif ev.get("evenement") == "RETOUR":
                self.local_states[ev.get("balise")] = "au râtelier"
                for reservation in self.offline_reservations.values():
                    if reservation.get("balise") == ev.get("balise"):
                        reservation["status"] = "TERMINEE"
                        reservation["retour_t"] = ev.get("t")
            elif ev.get("evenement") == "ETRANGERE":
                self.local_states[ev.get("balise")] = "A_REEQUILIBRER"
        self.dernier_tic_cloud = None
        print("station_exemple : décisions envoyées à %s" % CLOUD, file=sys.stderr)

    # -- un paquet radio arrive -------------------------------------------
    def observation(self, o):
        self.station = o.station
        self.current_t = max(self.current_t, float(o.t))
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

    def reinitialisation_flux(self, t, station="A"):
        """Oublie la scène en mémoire et transmet un événement RESET au cloud."""
        self.vues.clear()
        self.demarre = False
        self.station = station
        self.journal.clear()
        self.dernier_tic_cloud = None
        self.envoyer({"t": t, "station": station, "evenement": "RESET"})

    # -- appelée même quand plus rien n'arrive ----------------------------
    def tic(self, t):
        self.current_t = max(self.current_t, float(t))
        for balise, vue in list(self.vues.items()):
            if t - vue > SILENCE:  # silence prolongé : elle est partie
                del self.vues[balise]
                self.signaler("DEPART", balise, t)
        if self.vider():  # cadence cloud à l'horloge de la source
            maintenant = time.monotonic()
            if (
                self.dernier_tic_cloud is None
                or maintenant - self.dernier_tic_cloud >= 1.0
            ):
                self.envoyer({"t": t, "station": self.station, "evenement": "TIC"})
                self.dernier_tic_cloud = maintenant

    # -- sortie -----------------------------------------------------------
    def signaler(self, type_, balise, t):
        """Met à jour l’état physique et journalise avant de tenter l’envoi."""
        {"DEPART": self.depart, "RETOUR": self.retour, "ETRANGERE": self.etrangere}[
            type_
        ](balise, t, self.station)
        with lock:
            self.local_states[balise] = (
                "en mer"
                if type_ == "DEPART"
                else "A_REEQUILIBRER" if type_ == "ETRANGERE" else "au râtelier"
            )
            if type_ != "ETRANGERE":
                for reservation in self.offline_reservations.values():
                    if reservation.get("balise") == balise and reservation.get(
                        "status"
                    ) in ("ARMEE", "EN_COURS"):
                        reservation["status"] = (
                            "EN_COURS" if type_ == "DEPART" else "TERMINEE"
                        )
                        reservation["depart_t" if type_ == "DEPART" else "retour_t"] = t
            self.sauver_etat()
        event = {"t": t, "station": self.station, "balise": balise, "evenement": type_}
        event["event_id"] = hashlib.sha256(
            "\0".join(
                str(event.get(k, "")) for k in ("station", "evenement", "balise", "t")
            ).encode()
        ).hexdigest()
        with lock:
            self.journal.append(event)
            self.sauver_journal()
        self.vider()

    def sauver_journal(self):
        """Remplace atomiquement le journal après synchronisation sur disque."""
        temporary = JOURNAL_PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            for event in self.journal:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, JOURNAL_PATH)

    # -- si le réseau devenait inaccessible : rien ne se perd -------------
    def vider(self):
        """Envoie le journal dans l'ordre ; s'arrête au premier échec d'envoi."""
        while True:
            with lock:
                if not self.journal:
                    return True
                event = self.journal[0]
            if not self.envoyer(event):
                return False  # on réessaiera au prochain tic
            with lock:
                if self.journal and self.journal[0].get("event_id") == event.get(
                    "event_id"
                ):
                    self.journal.pop(0)
                    self.sauver_journal()

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

    # --- Autorisation et réservations hors ligne ---

    def offline_authorize(self, token):
        """Vérifie la signature et l’expiration réelle du jeton du cloud."""
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(
                OFFLINE_SECRET.encode(), encoded.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(
                base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            )
            if payload.get("expires", 0) < time.time():
                return None
            return payload
        except Exception:
            return None

    def offline_status(self, station):
        """Expose les planches de la station libres et hors maintenance."""
        with lock:
            boards = []
            for board in HOME_BOARDS.get(station, []):
                state = self.local_states.get(board, "au râtelier")
                if (
                    board not in self.maintenance
                    and state == "au râtelier"
                    and not any(
                        x.get("balise") == board
                        and x.get("status") in ("ARMEE", "EN_COURS")
                        for x in self.offline_reservations.values()
                    )
                ):
                    boards.append(board)
            return {
                "station": station,
                "t": self.current_t,
                "planches_disponibles": boards,
                "locations": list(self.offline_reservations.values()),
            }

    def refresh_cloud_status(self):
        """Actualise le parc sans écraser les locations encore suivies localement."""
        try:
            with urllib.request.urlopen(
                CLOUD.rsplit("/", 1)[0] + "/api/parc", timeout=0.25
            ) as response:
                remote = json.loads(response.read().decode())
            with lock:
                self.maintenance = {
                    b
                    for b, p in remote.get("planches", {}).items()
                    if p.get("statut") == "maintenance"
                }
                for board, p in remote.get("planches", {}).items():
                    if board not in {
                        x.get("balise")
                        for x in self.offline_reservations.values()
                        if x.get("status") in ("ARMEE", "EN_COURS")
                    }:
                        self.local_states[board] = p.get("statut", "au râtelier")
                self.sauver_etat()
        except Exception:
            pass

    def sauver_etat(self):
        """Conserve le dernier état même après acquittement du journal par le cloud."""
        temporary = STATE_PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "local_states": self.local_states,
                    "maintenance": sorted(self.maintenance),
                    "offline_reservations": self.offline_reservations,
                },
                f,
                ensure_ascii=False,
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, STATE_PATH)

    def reserver_hors_ligne(self, data):
        """Réserve localement et place la réservation avant ses futurs mouvements."""
        payload = self.offline_authorize(str(data.get("authorization", "")))
        if not payload:
            return None, "Autorisation hors ligne invalide ou expirée."
        station = str(data.get("station", "A"))
        if payload.get("station_id") != station:
            return None, "Cette autorisation concerne une autre station."
        with lock:
            prior = next(
                (
                    x
                    for x in self.offline_reservations.values()
                    if x.get("authorization_id") == payload["authorization_id"]
                    and x.get("status") in ("ARMEE", "EN_COURS")
                ),
                None,
            )
            if prior:
                return prior, None
            available = self.offline_status(station)["planches_disponibles"]
            if not available:
                return None, "Aucune planche disponible à cette station."
            board = sorted(available)[0]
            t = self.current_t or max(
                (float(x.get("t", 0)) for x in self.journal), default=0.0
            )
            reservation_id = uuid.uuid4().hex
            event = {
                "t": t,
                "station": station,
                "balise": board,
                "evenement": "OFFLINE_RESERVATION",
                "offline_reservation_id": reservation_id,
                "authorization_id": payload["authorization_id"],
                "status": "ARMEE",
            }
            event["event_id"] = hashlib.sha256(
                (reservation_id + "OFFLINE_RESERVATION").encode()
            ).hexdigest()
            self.journal.append(event)
            self.sauver_journal()
            self.offline_reservations[reservation_id] = event
            self.local_states[board] = "réservée"
            self.sauver_etat()
            return event, None


class OfflineAPI(BaseHTTPRequestHandler):
    """Expose les opérations client locales utilisées quand le cloud est muet."""

    def reply(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.reply({"ok": True})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/offline/status":
            return self.reply(self.server.station.offline_status("A"))
        if u.path == "/offline/locations":
            return self.reply(
                {"locations": self.server.station.offline_status("A")["locations"]}
            )
        return self.reply({"erreur": "Introuvable"}, 404)

    def do_POST(self):
        if urlparse(self.path).path != "/offline/arme":
            return self.reply({"erreur": "Introuvable"}, 404)
        try:
            data = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
            )
        except Exception:
            data = {}
        reservation, error = self.server.station.reserver_hors_ligne(data)
        if error:
            return self.reply({"erreur": error}, 403)
        return self.reply(
            {
                "etat": "armée",
                "balise": reservation["balise"],
                "station": reservation["station"],
                "offline_reservation_id": reservation["offline_reservation_id"],
            }
        )


def start_offline_api(station):
    """Démarre l’API locale et rafraîchit périodiquement l’instantané du parc."""
    server = ThreadingHTTPServer(("0.0.0.0", LOCAL_PORT), OfflineAPI)
    server.station = station
    threading.Thread(target=server.serve_forever, daemon=True).start()
    station.refresh_cloud_status()

    def refresh():
        while True:
            station.refresh_cloud_status()
            time.sleep(5)

    threading.Thread(target=refresh, daemon=True).start()
    print("API locale hors ligne : http://0.0.0.0:%d" % LOCAL_PORT, file=sys.stderr)


if __name__ == "__main__":
    instance = Station()
    start_offline_api(instance)
    lancer(lambda: instance)


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
# Le journal et l’état hors ligne sont désormais persistés sur disque ;
# les limites ci-dessus concernent toujours la détection radio.
