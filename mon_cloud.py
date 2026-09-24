#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud KORKO : stations compatibles, expérience client et tableau admin."""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT, TARIF_MIN, PLAFOND = 9000, 0.20, 600
PERDUE = 3 * PLAFOND
STATIQUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PARC = {"korko-01": "A", "korko-02": "A", "korko-03": "B", "korko-04": "B",
        "korko-05": "C", "korko-06": "C"}
planches = {b: {"origine": s, "ou": s, "statut": "au râtelier", "sorties": 0}
            for b, s in PARC.items()}
sessions, file_attente, reservations, client_sessions = {}, {}, {}, {}
journal, signes, horloge = [], {}, 0.0

def duree_txt(s):
    s = max(0, int(s))
    return "%d min" % (s // 60) if s < 3600 else "%d h %02d" % (s // 3600, s % 3600 // 60)

def note(texte):
    journal.insert(0, "[%7.1f] %s" % (horloge, texte))
    print("  %s" % journal[0], flush=True)

def sms(client, texte):
    note("SMS → %s : %s" % (client, texte))

def suggestion(station):
    dispo = [b for b, p in planches.items()
             if p["statut"] == "au râtelier" and p["ou"] == station and b not in sessions]
    return min(dispo, key=lambda b: planches[b]["sorties"]) if dispo else None

def armer(client, station, identifiant):
    ancien = client_sessions.get(identifiant)
    if ancien and ancien["etat"] in ("armée", "en cours"):
        return ancien, None
    balise = suggestion(station)
    if not balise:
        return None, "Plus de planche disponible à la station %s." % station
    planches[balise]["statut"] = "réservée"
    reservations[balise] = identifiant
    etat = {"client": client, "station": station, "balise": balise, "etat": "armée", "armee_a": horloge}
    client_sessions[identifiant] = etat
    note("%s arme en station %s → %s" % (client, station, balise))
    return etat, None

def cloturer(balise, station, t, hors_base):
    p = planches[balise]
    p["statut"], p["ou"] = "au râtelier", station
    s = sessions.pop(balise, None)
    if s:
        duree, montant = max(0, t - s["debut"]), max(0, t - s["debut"]) / 60 * TARIF_MIN
        identifiant = s.get("identifiant")
        if identifiant in client_sessions:
            client_sessions[identifiant].update({"etat": "retournée", "retour_a": t, "duree": duree, "montant": montant})
        sms(s["client"], "Merci ! %s, %s, %.2f €. Caution libérée." % (balise, duree_txt(duree), montant))
    if hors_base:
        note("RÉÉQUILIBRAGE : %s rendue en %s, sa base est %s" % (balise, station, p["origine"]))

def retards():
    for balise, s in list(sessions.items()):
        duree = horloge - s["debut"]
        if duree > PERDUE:
            sms(s["client"], "%s jamais rendue. Caution débitée : elle est à toi." % balise)
            planches[balise]["statut"] = "perdue"
            del sessions[balise]
        elif duree > PLAFOND and not s["rappel"]:
            s["rappel"] = True
            sms(s["client"], "Ta session tourne depuis %s. Raccroche %s en sortant." % (duree_txt(duree), balise))

def traiter(ev):
    global horloge
    horloge = max(horloge, ev.get("t", horloge))
    type_, station = ev.get("evenement"), ev.get("station")
    if station not in signes: note("station %s branchée" % station)
    signes[station] = ev.get("t", horloge)
    if type_ == "TIC":
        retards(); return
    balise = ev.get("balise")
    if balise not in planches: return note("balise inconnue : %s" % balise)
    if type_ == "DEPART":
        p = planches[balise]; p["statut"], p["ou"], p["sorties"] = "en mer", None, p["sorties"] + 1
        identifiant = reservations.pop(balise, None)
        if identifiant:
            experience = client_sessions.get(identifiant)
            client = experience["client"]
            experience.update({"etat": "en cours", "depart_a": ev["t"]})
        else:
            attente = file_attente.setdefault(station, [])
            if not attente:
                p["statut"] = "sortie sans client"
                return note("ALERTE : %s sortie de %s sans session armée" % (balise, station))
            client, identifiant = attente.pop(0), None
        sessions[balise] = {"client": client, "debut": ev["t"], "rappel": False, "identifiant": identifiant}
        note("DÉPART %s depuis %s — %s" % (balise, station, client))
    elif type_ in ("RETOUR", "ETRANGERE"):
        note("%s %s en %s" % (type_, balise, station))
        cloturer(balise, station, ev["t"], type_ == "ETRANGERE")

def client_json(identifiant):
    e = client_sessions.get(identifiant)
    if not e: return {"etat": "inconnue", "t": horloge}
    r = dict(e); r["t"] = horloge
    if r["etat"] == "en cours":
        r["duree"] = max(0, horloge - r["depart_a"])
        r["montant"] = r["duree"] / 60 * TARIF_MIN
    return r

def tableau():
    lignes = ["STATIONS", "--------"]
    lignes += ["%s   dernier message à t = %.0f s" % (st, t) for st, t in sorted(signes.items())] or ["Aucune station branchée"]
    lignes += ["", "PARC", "----"]
    for b, p in sorted(planches.items()):
        s = sessions.get(b)
        lignes.append("%-9s %-18s base %s  sorties %-3d %s" % (b, p["statut"], p["origine"], p["sorties"], "→ %s depuis %s" % (s["client"], duree_txt(horloge-s["debut"])) if s else ""))
    return "\n".join(lignes + ["", "JOURNAL", "-------"] + journal[:15])

class Cloud(BaseHTTPRequestHandler):
    def repondre(self, corps, type_="text/plain; charset=utf-8", code=200):
        data = corps.encode("utf-8") if isinstance(corps, str) else corps
        self.send_response(code); self.send_header("Content-Type", type_)
        self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)
    def json(self, objet, code=200):
        self.repondre(json.dumps(objet, ensure_ascii=False), "application/json; charset=utf-8", code)
    def lire_json(self):
        try: return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
        except (ValueError, UnicodeDecodeError): return None
    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/arme":
            d = self.lire_json()
            if not d or not d.get("client") or not d.get("identifiant"): return self.json({"erreur": "Numéro ou session manquant."}, 400)
            client = str(d["client"]).strip(); client = client if client.startswith("+") else "+" + client
            etat, erreur = armer(client, str(d.get("station", "A")), str(d["identifiant"]))
            return self.json({"erreur": erreur} if erreur else client_json(str(d["identifiant"])), 409 if erreur else 200)
        if u.path == "/evenements":
            brut = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
            for ligne in brut.strip().splitlines():
                try: traiter(json.loads(ligne))
                except Exception as e: note("événement illisible : %s" % e)
            return self.repondre("ok")
        self.repondre("Introuvable", code=404)
    def do_GET(self):
        u, q = urlparse(self.path), parse_qs(urlparse(self.path).query)
        if u.path in ("/parc", "/api/parc"): return self.json({"t": horloge, "planches": planches, "sessions": sessions})
        if u.path == "/api/client": return self.json(client_json(q.get("identifiant", [""])[0]))
        if u.path == "/arme":
            client = q.get("client", ["+33600000000"])[0].strip(); client = client if client.startswith("+") else "+" + client
            station, balise = q.get("station", ["A"])[0], suggestion(q.get("station", ["A"])[0])
            if not balise: return self.repondre("Plus de planche libre en station %s." % station)
            file_attente.setdefault(station, []).append(client); note("%s arme (mode historique) en station %s" % (client, station))
            return self.repondre("Prends la planche %s." % balise)
        if u.path == "/admin":
            return self.repondre("<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=2><title>KORKO admin</title><body style='font:14px ui-monospace,monospace;background:#ede3ce;color:#164b55;padding:24px'><h2>KORKO · administration · t = %.0f s</h2><pre>%s</pre></body>" % (horloge, tableau()), "text/html; charset=utf-8")
        if u.path in ("/", "/index.html"): return self.servir("index.html", "text/html; charset=utf-8")
        if u.path in ("/static/style.css", "/static/app.js"): return self.servir(os.path.basename(u.path), "text/css; charset=utf-8" if u.path.endswith("css") else "application/javascript; charset=utf-8")
        self.repondre("Introuvable", code=404)
    def servir(self, nom, type_):
        try:
            with open(os.path.join(STATIQUE, nom), "rb") as f: self.repondre(f.read(), type_)
        except OSError: self.repondre("Fichier statique introuvable", code=404)
    def log_message(self, *args): pass

if __name__ == "__main__":
    print("Cloud KORKO sur http://0.0.0.0:%d" % PORT)
    print("  expérience client : /     administration : /admin     parc brut : /parc")
    print("  stations : POST /evenements\n")
    HTTPServer(("0.0.0.0", PORT), Cloud).serve_forever()
