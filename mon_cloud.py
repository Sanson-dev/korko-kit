#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud KORKO : stations compatibles, expérience client et tableau admin."""
import json
import html
import os
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from korko import STATIONS
from paiement import caisse, horaires
from smart_contract import chaine

PORT, TARIF_MIN, PLAFOND = 9000, 0.20, 600
STATIQUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
REGISTRE = chaine.connecter()
PARC = chaine.lire_parc(REGISTRE, STATIONS)
planches = {b: {"origine": s, "ou": s, "statut": "au râtelier", "sorties": 0}
            for b, s in PARC.items()}
sessions, file_attente, reservations, client_sessions = {}, {}, {}, {}
rapports_planches = []
journal, signes, horloge = [], {}, 0.0

def duree_txt(s):
    s = max(0, int(s))
    return "%d min" % (s // 60) if s < 3600 else "%d h %02d" % (s // 3600, s % 3600 // 60)

def note(texte):
    ligne = "[%7.1f] %s" % (horloge, texte)
    journal.insert(0, ligne)
    print("  %s" % ligne, flush=True)

PUBLIEUR = chaine.Publieur(REGISTRE, chaine.compte_du_cloud(), note)
CAISSE = caisse.creer_caisse(note)

def sms(client, texte):
    note("SMS → %s : %s" % (client, texte))

def prevenir(session, texte):
    """Message au client d'une session : dans son appli s'il en a une."""
    experience = client_sessions.get(session.get("identifiant"))
    if experience:
        CAISSE.envoyer(experience, texte, horloge)
    else:
        sms(session["client"], texte)

def suggestion(station):
    dispo = [b for b, p in planches.items()
             if p["statut"] == "au râtelier" and p["ou"] == station
             and b not in sessions and b not in reservations]
    return min(dispo, key=lambda b: planches[b]["sorties"]) if dispo else None

def armer(client, moyen, station, identifiant):
    ancien = client_sessions.get(identifiant)
    if ancien and ancien["etat"] in ("armée", "en cours"):
        return ancien, None
    if not horaires.locations_ouvertes(horloge):
        return None, "Les locations ferment à 22 h. À demain !"
    balise = suggestion(station)
    if not balise:
        return None, "Plus de planche disponible à la station %s." % station
    etat = {"station": station, "balise": balise, "etat": "armée",
            "armee_a": horloge, "session_id": uuid.uuid4().hex}
    try:
        CAISSE.reserver(etat, client, moyen, horloge)
    except caisse.REFUS as refus:
        return None, str(refus)
    planches[balise]["statut"] = "réservée"
    reservations[balise] = identifiant
    client_sessions[identifiant] = etat
    note("%s arme en station %s → %s" % (etat["client"], station, balise))
    return etat, None

def annuler(identifiant):
    """Le client renonce avant de partir : planche libérée, rien de débité."""
    experience = client_sessions.get(identifiant)
    if not experience or experience["etat"] != "armée":
        return "Il n'y a pas de réservation à annuler."
    balise = experience["balise"]
    if reservations.get(balise) == identifiant:
        del reservations[balise]
        if planches[balise]["statut"] == "réservée":
            planches[balise]["statut"] = "au râtelier"
    experience["etat"] = "annulée"
    CAISSE.annuler(experience, horloge)
    note("%s annule sa réservation de %s" % (experience["client"], balise))
    return None

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
        experience = client_sessions.get(s.get("identifiant"))
        if experience:
            experience.update({"etat": "retournée", "retour_a": t, "duree": duree,
                               "montant": montant, "retour_station": station})
            CAISSE.terminer(experience, montant, t)
        elif not s["client"].startswith("Départ ambigu"):
            sms(s["client"], "Merci ! %s, %s, %.2f €." % (balise, duree_txt(duree), montant))
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
        if horloge >= s["limite"]:
            planches[balise]["statut"] = "perdue"
            del sessions[balise]
            note("ALERTE : %s pas rendue avant 23 h" % balise)
            experience = client_sessions.get(s.get("identifiant"))
            if experience:
                experience["etat"] = "caution débitée"
                CAISSE.saisir_caution(experience, horloge)
        elif duree > PLAFOND and not s["rappel"]:
            s["rappel"] = True
            prevenir(s, "Ta session tourne depuis %s. Raccroche %s en sortant." % (duree_txt(duree), balise))

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
    if type_ in ("DEPART", "RETOUR", "ETRANGERE"):
        PUBLIEUR.publier(ev)
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
            experience.update({"etat": "en cours", "depart_a": ev["t"]})
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
                    experience.update({"etat": "en cours", "depart_a": ev["t"]})
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
        sessions[balise] = {"client": client, "debut": ev["t"], "rappel": False, "identifiant": identifiant,
                            "limite": horaires.limite_retour(ev["t"])}
        note("DÉPART %s depuis %s — %s" % (balise, station, client))
    elif type_ in ("RETOUR", "ETRANGERE"):
        note("%s %s en %s" % (type_, balise, station))
        cloturer(balise, station, ev["t"], type_ == "ETRANGERE")

def client_json(identifiant):
    e = client_sessions.get(identifiant)
    if not e: return {"etat": "inconnue", "t": horloge, "heure": horaires.texte_heure(horloge)}
    r = {cle: valeur for cle, valeur in e.items() if cle not in ("autorisation", "identite")}
    r.update(CAISSE.resume(e))
    r["t"], r["heure"] = horloge, horaires.texte_heure(horloge)
    if r["etat"] == "caution débitée":
        r["montant"] = r["debite"]
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
        statut = p["statut"] + (" · À INSPECTER" if p.get("inspection") else "")
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
            return self.json({"erreur": erreur} if erreur else {"rapport": rapport}, 409 if erreur else 200)
        if u.path == "/api/reparer":
            d = self.lire_json()
            if not d or not d.get("balise"):
                return self.json({"erreur": "Planche manquante."}, 400)
            statut, erreur = reparer(str(d["balise"]))
            return self.json({"erreur": erreur} if erreur else {"statut": statut}, 409 if erreur else 200)
        if u.path == "/api/arme":
            d = self.lire_json()
            if (not d or not d.get("identifiant") or not isinstance(d.get("client"), dict)
                    or not isinstance(d.get("moyen"), dict)):
                return self.json({"erreur": "Identité ou moyen de paiement manquant."}, 400)
            etat, erreur = armer(d["client"], d["moyen"], str(d.get("station", "A")), str(d["identifiant"]))
            return self.json({"erreur": erreur} if erreur else client_json(str(d["identifiant"])), 409 if erreur else 200)
        if u.path == "/api/annuler":
            d = self.lire_json()
            if not d or not d.get("identifiant"):
                return self.json({"erreur": "Session manquante."}, 400)
            erreur = annuler(str(d["identifiant"]))
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
        if u.path in ("/parc", "/api/parc"):
            return self.json({"t": horloge, "heure": horaires.texte_heure(horloge),
                              "ouvert": horaires.locations_ouvertes(horloge), "planches": planches})
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
                if p["statut"] == "maintenance":
                    status = ("Réparation terminée — en attente du retour au râtelier"
                              if p.get("reparation_demandee") else "En maintenance")
                elif p["statut"] == "au râtelier":
                    status = "Disponible"
                else:
                    status = p["statut"]
                detail = ("<br><small>Dernier état : %s%s</small>" %
                          (html.escape(latest["condition"]), " — " + html.escape(latest.get("photo", "")) if latest.get("photo") else "") if latest else "")
                action = (" <button type='button' onclick=\"reparer('%s')\">Réparée — remettre en service</button>" % html.escape(b, quote=True)
                          if p["statut"] == "maintenance" and not p.get("reparation_demandee") else "")
                items.append("<li><strong>%s</strong> — %s%s%s</li>" % (html.escape(b), html.escape(status), detail, action))
            fiches = "".join("<li>%s</li>" % html.escape(ligne) for ligne in CAISSE.fiches()) or "<li>Aucun client</li>"
            page = "<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=2><title>KORKO admin</title><body style='font:14px ui-monospace,monospace;background:#ede3ce;color:#164b55;padding:24px'><h2>KORKO · administration · %s · t = %.0f s</h2><section><h3>ÉTAT DES PLANCHES</h3><ul style='padding-left:20px;line-height:1.8'>%s</ul></section><section><h3>FICHES CLIENTS</h3><ul style='padding-left:20px;line-height:1.8'>%s</ul></section><section><h3>RAPPORTS DE CONDITION</h3><ul style='padding-left:20px;line-height:1.8'>%s</ul></section><pre>%s</pre><style>.damaged{color:#a13225;font-weight:bold;background:#f4d8cf}</style><script>async function reparer(b){const r=await fetch('/api/reparer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({balise:b})});const d=await r.json();if(!r.ok)alert(d.erreur);else location.reload()}</script></body>" % (horaires.texte_heure(horloge), horloge, "".join(items), fiches, rapports, html.escape(tableau()))
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
    PUBLIEUR.start()
    print("Cloud KORKO sur http://0.0.0.0:%d" % PORT)
    print("  registre : %s" % chaine.lien_adresse(REGISTRE.address))
    print("  expérience client : /     administration : /admin     parc brut : /parc")
    print("  stations : POST /evenements\n")
    HTTPServer(("0.0.0.0", PORT), Cloud).serve_forever()
