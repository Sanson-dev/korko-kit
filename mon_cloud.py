#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud KORKO : stations compatibles, expérience client et tableau admin."""
import json
import html
import os
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT, TARIF_MIN, PLAFOND = 9000, 0.20, 600
RESERVATION_TIMEOUT = 120
PERDUE = 3 * PLAFOND
STATIQUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PARC = {"korko-01": "A", "korko-02": "A", "korko-03": "B", "korko-04": "B",
        "korko-05": "C", "korko-06": "C"}
planches = {b: {"origine": s, "ou": s, "statut": "au râtelier", "sorties": 0}
            for b, s in PARC.items()}
sessions, file_attente, reservations, client_sessions = {}, {}, {}, {}
rapports_planches = []
journal, signes, horloge = [], {}, 0.0
demo_generation = 0

def reinitialiser_demo():
    """Efface l'état temporaire du cloud et restaure le parc prototype."""
    global horloge, demo_generation
    demo_generation += 1
    sessions.clear()
    file_attente.clear()
    reservations.clear()
    client_sessions.clear()
    rapports_planches.clear()
    journal.clear()
    signes.clear()
    horloge = 0.0
    planches.clear()
    planches.update({b: {"origine": s, "ou": s, "statut": "au râtelier", "sorties": 0}
                     for b, s in PARC.items()})

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
             if p["statut"] == "au râtelier" and p["ou"] == station
             and b not in sessions and b not in reservations]
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
    etat = {"client": client, "station": station, "balise": balise, "etat": "armée",
            "armee_a": horloge, "reservation_t": horloge, "reservation_status": "Réservée",
            "session_id": uuid.uuid4().hex}
    client_sessions[identifiant] = etat
    note("%s arme en station %s → %s" % (client, station, balise))
    return etat, None

def cloturer(balise, station, t, hors_base):
    p = planches[balise]
    p["ou"] = station
    if p["statut"] != "maintenance":
        p["statut"] = "au râtelier"
    elif p.get("reparation_demandee") and station == p["origine"]:
        p["statut"] = "au râtelier"
        p.pop("reparation_demandee", None)
        note("RÉPARATION VALIDÉE : %s de retour à son râtelier %s" % (balise, station))
    s = sessions.pop(balise, None)
    if s:
        duree, montant = max(0, t - s["debut"]), max(0, t - s["debut"]) / 60 * TARIF_MIN
        identifiant = s.get("identifiant")
        if identifiant in client_sessions:
            client_sessions[identifiant].update({"etat": "retournée", "retour_a": t, "duree": duree,
                                                  "montant": montant, "retour_station": station})
        if not s["client"].startswith("Départ ambigu"):
            sms(s["client"], "Merci ! %s, %s, %.2f €. Caution libérée." % (balise, duree_txt(duree), montant))
    if hors_base:
        note("RÉÉQUILIBRAGE : %s rendue en %s, sa base est %s" % (balise, station, p["origine"]))

def enregistrer_rapport(identifiant, session_id, condition, photo=None):
    experience = client_sessions.get(identifiant)
    if not experience or experience.get("etat") != "retournée" or experience.get("session_id") != session_id:
        return None, "Cette session n'est plus disponible."
    if condition not in ("OK", "MINOR", "DAMAGED"):
        return None, "Choisissez un état de planche."
    if any(r["session_id"] == session_id for r in rapports_planches):
        return None, "Merci, votre retour a déjà été envoyé."
    rapport = {"session_id": session_id, "balise": experience["balise"],
               "station": experience.get("retour_station", experience["station"]),
               "t": experience["retour_a"], "condition": condition}
    if photo:
        rapport["photo"] = photo[:255]
    rapports_planches.insert(0, rapport)
    planche = planches[rapport["balise"]]
    if condition == "MINOR":
        planche["inspection"] = True
    elif condition == "OK":
        planche.pop("inspection", None)
    else:
        planche["statut"] = "maintenance"
        planche.pop("inspection", None)
    experience["rapport_condition"] = condition
    note("ÉTAT DE PLANCHE : %s — %s (retour t=%.0f)" % (rapport["balise"], condition, rapport["t"]))
    return rapport, None

def reparer(balise):
    planche = planches.get(balise)
    if not planche or planche["statut"] != "maintenance":
        return None, "Cette planche n'est pas en maintenance."
    planche["reparation_demandee"] = True
    if planche.get("ou") == planche["origine"] and balise not in sessions:
        planche["statut"] = "au râtelier"
        planche.pop("reparation_demandee", None)
        note("RÉPARATION VALIDÉE : %s déjà présente à son râtelier %s" % (balise, planche["origine"]))
        return "Disponible", None
    note("RÉPARATION TERMINÉE : %s en attente de son râtelier %s" % (balise, planche["origine"]))
    return "Réparation terminée — en attente du retour au râtelier", None

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

def expirations_reservations():
    for identifiant, e in list(client_sessions.items()):
        if (e.get("etat") != "armée" or e.get("reservation_status") != "Réservée"
                or horloge - e.get("reservation_t", horloge) < RESERVATION_TIMEOUT):
            continue
        balise = e["balise"]
        if reservations.get(balise) != identifiant:
            continue
        reservations.pop(balise)
        p = planches[balise]
        if p["statut"] == "réservée" and balise not in sessions:
            p["statut"] = "au râtelier"
        e.update({"etat": "expirée", "reservation_status": "Expirée", "expiree_a": horloge})
        note("Réservation %s expirée après %d s" % (balise, RESERVATION_TIMEOUT))

def traiter(ev):
    global horloge
    horloge = max(horloge, ev.get("t", horloge))
    type_, station = ev.get("evenement"), ev.get("station")
    if station not in signes: note("station %s branchée" % station)
    signes[station] = ev.get("t", horloge)
    expirations_reservations()
    if type_ == "TIC":
        retards(); return
    balise = ev.get("balise")
    if balise not in planches: return note("balise inconnue : %s" % balise)
    if type_ == "DEPART":
        p = planches[balise]
        if p["statut"] == "maintenance":
            p["ou"] = None
            return note("ALERTE : départ de %s ignoré, planche en maintenance" % balise)
        if balise in sessions or p["statut"] in ("en mer", "départ ambigu"):
            return note("ALERTE : départ dupliqué ignoré pour %s, déjà associé à une session" % balise)

        identifiant = reservations.get(balise)
        experience = client_sessions.get(identifiant) if identifiant else None
        if experience and experience["etat"] == "armée" and experience["station"] == station:
            reservations.pop(balise, None)
            client = experience["client"]
            experience.update({"etat": "en cours", "reservation_status": "En cours", "depart_a": ev["t"]})
        elif (not identifiant and p["statut"] == "au râtelier" and p["ou"] == station
              and balise not in sessions):
            candidates = [(i, e) for i, e in client_sessions.items()
                          if e["etat"] == "armée" and e["station"] == station]
            legacy = file_attente.setdefault(station, [])
            if len(candidates) == 1 and not legacy:
                identifiant, experience = candidates[0]
                ancien = experience["balise"]
                if (ancien in reservations and reservations[ancien] == identifiant
                        and ancien not in sessions and planches[ancien]["statut"] == "réservée"):
                    del reservations[ancien]
                    planches[ancien]["statut"] = "au râtelier"
                    planches[ancien]["ou"] = station
                    reservations[balise] = identifiant
                    experience.update({"balise": balise, "ecart": "Assigned %s -> actual departure %s" % (ancien, balise),
                                       "message": "Vous avez pris la planche %s. Aucun problème, votre session a été mise à jour." % balise.replace("korko-", "")})
                    note("PLANCHE MISE À JOUR : %s -> actual departure %s (%s)" % (ancien, balise, experience["client"]))
                    reservations.pop(balise, None)
                    client = experience["client"]
                    experience.update({"etat": "en cours", "reservation_status": "En cours", "depart_a": ev["t"]})
                else:
                    candidates.append((None, {}))
            if len(candidates) > 1 or (candidates and legacy):
                client, identifiant = "Départ ambigu — clients à vérifier", None
                experience = None
                note("AMBIGUÏTÉ : départ de %s en %s, %d sessions armées et %d client(s) historique(s)" %
                     (balise, station, len(candidates), len(legacy)))
            elif not candidates and legacy:
                client, identifiant = legacy.pop(0), None
            elif not candidates:
                p["statut"] = "sortie sans client"
                return note("ALERTE : %s sortie de %s sans session armée" % (balise, station))
        else:
            return note("ALERTE : départ de %s ignoré, planche indisponible ou réservée" % balise)

        p["statut"], p["ou"], p["sorties"] = "en mer", None, p["sorties"] + 1
        sessions[balise] = {"client": client, "debut": ev["t"], "rappel": False, "identifiant": identifiant}
        note("DÉPART %s depuis %s — %s" % (balise, station, client))
    elif type_ in ("RETOUR", "ETRANGERE"):
        note("%s %s en %s" % (type_, balise, station))
        cloturer(balise, station, ev["t"], type_ == "ETRANGERE")

def client_json(identifiant):
    e = client_sessions.get(identifiant)
    if not e: return {"etat": "inconnue", "t": horloge, "generation": demo_generation}
    r = dict(e); r["t"] = horloge; r["generation"] = demo_generation
    if r["etat"] == "en cours":
        r["duree"] = max(0, horloge - r["depart_a"])
        r["montant"] = r["duree"] / 60 * TARIF_MIN
    if r.get("reservation_status") == "Réservée":
        r["reservation_restante"] = max(0, RESERVATION_TIMEOUT - (horloge - r.get("reservation_t", horloge)))
    return r

def tableau():
    lignes = ["STATIONS", "--------"]
    lignes += ["%s   dernier message à t = %.0f s" % (st, t) for st, t in sorted(signes.items())] or ["Aucune station branchée"]
    lignes += ["", "PARC", "----"]
    for b, p in sorted(planches.items()):
        s = sessions.get(b)
        statut = p["statut"] + (" · À INSPECTER" if p.get("inspection") else "")
        reservation = next((e for e in reversed(list(client_sessions.values())) if e.get("balise") == b and e.get("reservation_status") in ("Réservée", "En cours", "Expirée")), None)
        if reservation:
            statut += " · réservation %s" % reservation["reservation_status"]
        lignes.append("%-9s %-18s base %s  sorties %-3d %s" % (b, statut, p["origine"], p["sorties"], "→ %s depuis %s" % (s["client"], duree_txt(horloge-s["debut"])) if s else ""))
    lignes += ["", "ÉCARTS D'ATTRIBUTION", "-------------------"]
    ecarts = ["%s — %s" % (e["client"], e["ecart"]) for e in client_sessions.values() if e.get("ecart")]
    ambigu = ["Départ ambigu : %s" % b for b, s in sessions.items() if s.get("identifiant") is None and s["client"].startswith("Départ ambigu")]
    lignes += ecarts + ambigu if ecarts or ambigu else ["Aucun"]
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
        if u.path == "/api/condition":
            d = self.lire_json()
            if not d or not d.get("identifiant") or not d.get("session_id"):
                return self.json({"erreur": "Session manquante."}, 400)
            photo = d.get("photo")
            if photo is not None and not isinstance(photo, str):
                return self.json({"erreur": "Photo invalide."}, 400)
            rapport, erreur = enregistrer_rapport(str(d["identifiant"]), str(d["session_id"]),
                                                    d.get("condition"), photo)
            return self.json({"erreur": erreur, "generation": demo_generation} if erreur else
                             {"rapport": rapport, "generation": demo_generation}, 409 if erreur else 200)
        if u.path == "/api/reparer":
            d = self.lire_json()
            if not d or not d.get("balise"):
                return self.json({"erreur": "Planche manquante."}, 400)
            statut, erreur = reparer(str(d["balise"]))
            return self.json({"erreur": erreur} if erreur else {"statut": statut}, 409 if erreur else 200)
        if u.path == "/api/admin/reset-demo":
            d = self.lire_json()
            if not d or d.get("confirmation") != "REINITIALISER":
                return self.json({"erreur": "Confirmation requise."}, 400)
            reinitialiser_demo()
            return self.json({"message": "Cloud réinitialisé.", "generation": demo_generation})
        if u.path == "/api/arme":
            d = self.lire_json()
            if not d or not d.get("client") or not d.get("identifiant"): return self.json({"erreur": "Numéro ou session manquant."}, 400)
            client = str(d["client"]).strip(); client = client if client.startswith("+") else "+" + client
            if d.get("generation") != demo_generation:
                return self.json({"erreur": "La démonstration a été réinitialisée.", "generation": demo_generation}, 409)
            etat, erreur = armer(client, str(d.get("station", "A")), str(d["identifiant"]))
            return self.json({"erreur": erreur, "generation": demo_generation} if erreur else client_json(str(d["identifiant"])), 409 if erreur else 200)
        if u.path == "/evenements":
            brut = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
            for ligne in brut.strip().splitlines():
                try: traiter(json.loads(ligne))
                except Exception as e: note("événement illisible : %s" % e)
            return self.repondre("ok")
        self.repondre("Introuvable", code=404)
    def do_GET(self):
        u, q = urlparse(self.path), parse_qs(urlparse(self.path).query)
        if u.path in ("/parc", "/api/parc"): return self.json({"t": horloge, "generation": demo_generation, "planches": planches, "sessions": sessions})
        if u.path == "/api/client": return self.json(client_json(q.get("identifiant", [""])[0]))
        if u.path == "/arme":
            client = q.get("client", ["+33600000000"])[0].strip(); client = client if client.startswith("+") else "+" + client
            station, balise = q.get("station", ["A"])[0], suggestion(q.get("station", ["A"])[0])
            if not balise: return self.repondre("Plus de planche libre en station %s." % station)
            file_attente.setdefault(station, []).append(client); note("%s arme (mode historique) en station %s" % (client, station))
            return self.repondre("Prends la planche %s." % balise)
        if u.path == "/admin":
            rapports = "".join("<li class='%s'>%s | %s | retour t=%.0f%s</li>" %
                               ("damaged" if r["condition"] == "DAMAGED" else "", html.escape(r["balise"]),
                                html.escape(r["condition"]), r["t"],
                                " | photo prototype: " + html.escape(r["photo"]) if r.get("photo") else "")
                               for r in rapports_planches[:10]) or "<li>Aucun retour reçu</li>"
            items = []
            for b, p in sorted(planches.items()):
                latest = next((r for r in rapports_planches if r["balise"] == b), None)
                reservation = next((e for e in reversed(list(client_sessions.values())) if e.get("balise") == b and e.get("reservation_status") in ("Réservée", "En cours", "Expirée")), None)
                if p["statut"] == "maintenance":
                    status = ("Réparation terminée — en attente du retour au râtelier"
                              if p.get("reparation_demandee") else "En maintenance")
                elif p["statut"] == "au râtelier":
                    status = "Disponible"
                else:
                    status = p["statut"]
                detail = ("<br><small>Dernier état : %s%s</small>" %
                          (html.escape(latest["condition"]), " — " + html.escape(latest.get("photo", "")) if latest.get("photo") else "") if latest else "")
                if reservation:
                    detail += "<br><small>Réservation : %s</small>" % html.escape(reservation["reservation_status"])
                action = (" <button type='button' onclick=\"reparer('%s')\">Réparée — remettre en service</button>" % html.escape(b, quote=True)
                          if p["statut"] == "maintenance" and not p.get("reparation_demandee") else "")
                items.append("<li><strong>%s</strong> — %s%s%s</li>" % (html.escape(b), html.escape(status), detail, action))
            page = "<!doctype html><meta charset=utf-8><title>KORKO admin</title><body style='font:14px ui-monospace,monospace;background:#ede3ce;color:#164b55;padding:24px'><h2>KORKO · administration · t = %.0f s</h2><section><h3>ÉTAT DES PLANCHES</h3><ul style='padding-left:20px;line-height:1.8'>%s</ul></section><section><h3>RAPPORTS DE CONDITION</h3><ul style='padding-left:20px;line-height:1.8'>%s</ul></section><pre>%s</pre><section style='margin-top:32px;padding:18px;border:2px solid #bd744c;border-radius:12px;background:#fffaf0'><h3>DÉMO</h3><button id='reset-start' type='button'>Réinitialiser la démo</button><div id='reset-confirm' hidden><p>Réinitialiser toutes les données temporaires de la démo ?</p><p>Les locations, réservations et états temporaires seront effacés.</p><button id='reset-cancel' type='button'>Annuler</button> <button id='reset-submit' type='button'>Réinitialiser</button></div><p>Efface les locations, réservations, sessions clients, files d'attente, rapports de condition, alertes/journal, compteurs de sorties et signaux de stations. Les états de maintenance et d'inspection sont remis à zéro. Aucun fichier ni simulateur physique n'est modifié.</p><p id='reset-message' role='status' aria-live='polite'></p></section><style>.damaged{color:#a13225;font-weight:bold;background:#f4d8cf}button{padding:10px 14px;cursor:pointer}</style><script>async function reparer(b){const r=await fetch('/api/reparer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({balise:b})});const d=await r.json();if(!r.ok)alert(d.erreur);else location.reload()}const start=document.getElementById('reset-start'),box=document.getElementById('reset-confirm'),msg=document.getElementById('reset-message');start.onclick=()=>{box.hidden=false;start.disabled=true};document.getElementById('reset-cancel').onclick=()=>{box.hidden=true;start.disabled=false};document.getElementById('reset-submit').onclick=async()=>{const b=document.getElementById('reset-submit');b.disabled=true;try{const r=await fetch('/api/admin/reset-demo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation:'REINITIALISER'})});const d=await r.json();if(!r.ok)throw Error(d.erreur);sessionStorage.setItem('korko-reset-message',JSON.stringify({message:d.message,until:Date.now()+10000}));location.reload()}catch(e){msg.textContent=e.message;b.disabled=false}};const saved=JSON.parse(sessionStorage.getItem('korko-reset-message')||'null');if(saved&&saved.until>Date.now())msg.textContent=saved.message;else sessionStorage.removeItem('korko-reset-message');function refresh(){setTimeout(()=>{if(box.hidden)location.reload();else refresh()},2000)}refresh();</script></body>" % (horloge, "".join(items), rapports, tableau())
            return self.repondre(page, "text/html; charset=utf-8")
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
