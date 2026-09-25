#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud KORKO : stations compatibles, expérience client et tableau admin.

Mode hors ligne et persistance SQLite (branche de Sanson), registre des
planches sur Avalanche (smart_contract/) et paiement avec caution
(paiement/).
"""
import json
import html
import hashlib
import os
import time
import uuid
import sqlite3
import hmac
import base64
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from korko import STATIONS
from paiement import caisse, horaires, prestataire
from smart_contract import chaine

import photo_verification as photo
import photo_queue as file_photos

# --- Paramètres de service et état global du cloud ---
PORT, TARIF_MIN = 9000, 0.20
STATIQUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
REGISTRE = chaine.connecter()
PARC = chaine.lire_parc(REGISTRE, STATIONS)

# État mémoire du système : planches, sessions actives, réservations, clients et journal.
planches = {b: {"origine": s, "ou": s, "statut": "au râtelier", "sorties": 0}
            for b, s in PARC.items()}
sessions, file_attente, reservations, client_sessions = {}, {}, {}, {}
rapports_planches = []
journal, signes, horloge = [], {}, 0.0
rebalancements_actifs, historique_etrangeres = {}, []
dernier_contact = {}
evenements_recus = set()
demo_generation = 0
# Une connexion par fil (une connexion muette ne bloque plus les autres),
# mais un seul traitement à la fois : l'état ci-dessus est partagé.
VERROU = threading.Lock()
state_lock = threading.RLock()
photo_tasks = file_photos.FilePhotos()
DB_PATH = os.environ.get("KORKO_CLOUD_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "korko_cloud.sqlite3"))
OFFLINE_SECRET = os.environ.get("KORKO_OFFLINE_SECRET", "korko-hackathon-offline-secret")

#: ce que l'appli affiche d'une location sans caution (réservation hors ligne
#: d'un client dont le moyen de paiement n'a pas pu être retrouvé)
SANS_PAIEMENT = {"paiement": {"type": "aucun", "libelle": "aucun moyen enregistré"},
                 "caution": {"montant": prestataire.CAUTION, "etat": "échec", "liens": []},
                 "debite": None}

def autorisation_hors_ligne(identifiant, station):
    payload = json.dumps({"authorization_id": identifiant, "station_id": station,
                          "expires": int(time.time()) + 30 * 86400}, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(OFFLINE_SECRET.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature

# --- Persistance SQLite de l'état du cloud ---
def sauvegarder():
    # On sérialise les structures principales pour éviter de perdre l'état entre redémarrages.
    data = {k: globals()[k] for k in ("planches", "sessions", "file_attente", "reservations", "client_sessions", "rapports_planches", "journal", "signes", "horloge", "demo_generation", "rebalancements_actifs", "historique_etrangeres")}
    data["evenements_recus"] = list(evenements_recus)
    # default=vars : les autorisations de paiement sont enregistrées comme des dictionnaires.
    texte = json.dumps(data, ensure_ascii=False, default=vars)
    with sqlite3.connect(DB_PATH, timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS etat (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        db.execute("INSERT INTO etat(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (texte,))

def charger():
    # Charge l'état précédent au démarrage. Si aucun fichier n'existe, on repart sur l'état par défaut.
    global horloge, demo_generation, evenements_recus
    if not os.path.exists(DB_PATH): return
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute("CREATE TABLE IF NOT EXISTS etat (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
            row = db.execute("SELECT data FROM etat WHERE id=1").fetchone()
        if not row: return
        data = json.loads(row[0])
        for key in ("planches", "sessions", "file_attente", "reservations", "client_sessions", "rapports_planches", "journal", "signes", "rebalancements_actifs", "historique_etrangeres"):
            globals()[key] = data.get(key, globals()[key])
        horloge = data.get("horloge", 0.0); demo_generation = data.get("demo_generation", 0)
        evenements_recus = set(data.get("evenements_recus", []))
        for experience in client_sessions.values():
            if isinstance(experience.get("autorisation"), dict):
                experience["autorisation"] = prestataire.Autorisation.depuis(experience["autorisation"])
    except (sqlite3.Error, ValueError) as exc:
        raise RuntimeError("Impossible de charger l'état cloud %s: %s" % (DB_PATH, exc))

def reinitialiser_demo():
    """Efface l'état temporaire du cloud et restaure le parc prototype."""
    global horloge, demo_generation
    for experience in client_sessions.values():
        # une caution encore bloquée est libérée (en crypto, rendue au client)
        if experience.get("etat") in ("armée", "en cours") and "autorisation" in experience:
            CAISSE.prestataire.debiter(experience["autorisation"], 0.0)
    demo_generation += 1
    photo_tasks.set_demo_generation(demo_generation)
    sessions.clear()
    file_attente.clear()
    reservations.clear()
    client_sessions.clear()
    rapports_planches.clear()
    rebalancements_actifs.clear()
    historique_etrangeres.clear()
    journal.clear()
    signes.clear()
    dernier_contact.clear()
    evenements_recus.clear()
    horloge = 0.0
    planches.clear()
    planches.update({b: {"origine": s, "ou": s, "statut": "au râtelier", "sorties": 0}
                     for b, s in PARC.items()})
    sauvegarder()

def duree_txt(s):
    s = max(0, int(s))
    return "%d min" % (s // 60) if s < 3600 else "%d h %02d" % (s // 3600, s % 3600 // 60)

def note(texte):
    ligne = "[%7.1f] %s" % (horloge, texte)
    journal.insert(0, ligne)
    print("  %s" % ligne, flush=True)

def photo_note(texte):
    with state_lock:
        note(texte)

PUBLIEUR = chaine.creer_publieur(REGISTRE, note)
CAISSE = caisse.creer_caisse(note)

def sms(client, texte):
    note("SMS → %s : %s" % (client, texte))

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
            "armee_a": horloge, "reservation_t": horloge,
            "reservation_status": "Réservée", "session_id": uuid.uuid4().hex}
    try:
        client = dict(client, appareil=identifiant)
        CAISSE.reserver(etat, client, moyen, horloge)
    except caisse.REFUS as refus:
        return None, str(refus)
    planches[balise]["statut"] = "réservée"
    reservations[balise] = identifiant
    client_sessions[identifiant] = etat
    note("%s arme en station %s → %s" % (etat["client"], station, balise))
    return etat, None

def annuler(identifiant, expiree=False):
    """Le client renonce avant de partir, ou n'a pas pris la planche dans
    les 10 minutes : planche libérée, rien de débité.
    Retourne None, ou la phrase d'erreur à afficher."""
    experience = client_sessions.get(identifiant)
    if not experience or experience["etat"] != "armée":
        return "Il n'y a pas de réservation à annuler."
    if experience.get("offline") and not expiree:
        # la station, qui l'a donnée hors ligne, n'apprendrait pas l'annulation
        return "Cette réservation a été faite à la station : elle ne s'annule pas."
    balise = experience["balise"]
    if reservations.get(balise) == identifiant:
        del reservations[balise]
        if planches[balise]["statut"] == "réservée":
            planches[balise]["statut"] = "au râtelier"
    experience["etat"] = "annulée"
    experience["reservation_status"] = "Expirée" if expiree else "Annulée"
    if "autorisation" in experience:
        (CAISSE.expirer if expiree else CAISSE.annuler)(experience, horloge)
    note("%s : réservation de %s %s" % (experience["client"], balise,
                                        "expirée" if expiree else "annulée"))
    return None

def cloturer(balise, station, t, hors_base):
    p = planches[balise]
    s = sessions.get(balise)

    # Un retour physique doit être accepté même sans session active :
    # c'est le cas des départs sans client ou des états historisés de retard.
    if not s:
        if p["statut"] == "au râtelier" and p.get("ou") == station:
            note("RETOUR DUPLIQUÉ : %s déjà au râtelier en %s" % (balise, station))
            reservations.pop(balise, None)
            return

        if p["statut"] in ("sortie sans client", "retard", "perdue"):
            p["ou"] = station
            p["statut"] = "au râtelier"
            reservations.pop(balise, None)
            note("RETOUR %s sans session — remise au râtelier" % balise)
            if hors_base:
                note("RÉÉQUILIBRAGE : %s rendue en %s, sa base est %s" % (balise, station, p["origine"]))
            return

        # Un retour de planche sans session active ne doit pas générer un faux client.
        for identifiant, experience in list(client_sessions.items()):
            if experience.get("balise") == balise and experience.get("etat") in ("retournée", "retour_tardif", "terminée"):
                note("RETOUR DUPLIQUÉ : %s déjà clôturée, aucun changement appliqué" % balise)
                reservations.pop(balise, None)
                return
        note("ALERTE : retour de %s sans session active, remis au râtelier" % balise)
        p["ou"] = station
        p["statut"] = "au râtelier"
        reservations.pop(balise, None)
        return

    p["ou"] = station
    if p["statut"] != "maintenance":
        p["statut"] = "au râtelier"
    elif p.get("reparation_demandee") and station == p["origine"]:
        p["statut"] = "au râtelier"
        p.pop("reparation_demandee", None)
        note("RÉPARATION VALIDÉE : %s de retour à son râtelier %s" % (balise, station))

    duree, montant = max(0, t - s["debut"]), max(0, t - s["debut"]) / 60 * TARIF_MIN
    identifiant = s.get("identifiant")
    experience = client_sessions.get(identifiant)
    if experience:
        if experience.get("etat") in ("retournée", "retour_tardif", "terminée"):
            return note("RETOUR DUPLIQUÉ : %s déjà clôturée pour %s" % (balise, identifiant))
        experience.update({"etat": "retournée", "retour_a": t, "duree": duree,
                           "montant": montant, "retour_station": station,
                           "reservation_status": "Terminée"})
        # A reservation can be reassigned to the board actually taken.
        # Clear every mapping for this customer when the rental closes.
        for reservee, proprietaire in list(reservations.items()):
            if proprietaire == identifiant:
                reservations.pop(reservee, None)
    sessions.pop(balise, None)
    if experience and "autorisation" in experience:
        if s.get("perdue_signalee"):
            CAISSE.retour_tardif(experience, t)
        else:
            CAISSE.terminer(experience, montant, t)
    elif not s["client"].startswith("Départ ambigu"):
        sms(s["client"], "Merci ! %s, %s, %.2f €." % (balise, duree_txt(duree), montant))
    if hors_base:
        note("RÉÉQUILIBRAGE : %s rendue en %s, sa base est %s" % (balise, station, p["origine"]))

def signaler_etrangere(balise, station, t, event_id):
    """Enregistre un constat physique étranger sans toucher aux locations."""
    p = planches[balise]
    origine = p["origine"]
    if station == origine:
        return note("ETRANGERE incohérente ignorée : %s détectée à sa station %s" % (balise, station))
    if balise not in sessions:
        p["ou"] = station
        if p["statut"] == "au râtelier":
            p["statut"] = "A_REEQUILIBRER"
    # Une alerte active par planche; les événements sources restent en historique.
    if balise not in rebalancements_actifs:
        incident = {"balise": balise, "origine": origine, "station": station,
                    "t": t, "event_id": event_id}
        rebalancements_actifs[balise] = incident
        historique_etrangeres.append(dict(incident))
        note("ETRANGERE : %s, base %s, détectée en %s (t=%.1f)" % (balise, origine, station, t))

def confirmer_rebalancement(balise):
    if balise in sessions:
        return None, "Cette planche est encore en location. Attendez son retour."
    incident = rebalancements_actifs.pop(balise, None)
    if not incident:
        return None, "Aucun rééquilibrage actif pour cette planche."
    p = planches[balise]
    p["ou"] = p["origine"]
    if p["statut"] == "A_REEQUILIBRER": p["statut"] = "au râtelier"
    note("RÉÉQUILIBRAGE CONFIRMÉ : %s rétablie à sa station %s" % (balise, p["origine"]))
    return "Rééquilibrage confirmé", None

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
        if rapport["balise"] in reservations:
            annuler(reservations[rapport["balise"]])
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

def expirer_reservations():
    """Une planche réservée mais pas prise à temps (10 min) est libérée."""
    for identifiant, e in list(client_sessions.items()):
        limite = e["armee_a"] + horaires.DELAI_RESERVATION
        if e["etat"] == "armée" and horloge >= limite:
            annuler(identifiant, expiree=True)

def retards():
    """Réservations expirées, et planches pas rendues avant l'heure limite."""
    expirer_reservations()
    for balise, s in list(sessions.items()):
        if horloge < s["limite"]:
            continue

        planches[balise]["statut"] = "perdue"

        # La session reste active : un retour tardif doit encore pouvoir être
        # reconnu, clôturé et traité normalement.
        if s.get("perdue_signalee"):
            continue
        s["perdue_signalee"] = True

        note("ALERTE : %s pas rendue avant 23 h" % balise)
        experience = client_sessions.get(s.get("identifiant"))
        if experience:
            experience["etat"] = "caution débitée"
            if "autorisation" in experience:
                CAISSE.saisir_caution(experience, horloge)

def reserver_hors_ligne(ev, station):
    """Rejoue une réservation faite à la station pendant une coupure du cloud.

    La caution est bloquée maintenant, sur le moyen de paiement enregistré
    du téléphone qui a réservé (le jeton hors ligne ne s'obtient qu'après
    une location en ligne réussie)."""
    identifiant = str(ev.get("authorization_id", ""))
    balise = ev.get("balise")
    if not identifiant or balise not in planches:
        return note("réservation hors ligne invalide")
    ancien = client_sessions.get(identifiant)
    if ancien and ancien.get("etat") in ("armée", "en cours"):
        return
    etat = {"station": station, "balise": balise, "etat": "armée",
            "armee_a": ev["t"], "reservation_t": ev["t"],
            "reservation_status": "Réservée", "offline": True,
            "session_id": ev.get("offline_reservation_id", uuid.uuid4().hex)}
    fiche = CAISSE.fichier.par_appareil(identifiant)
    identite = {cle: fiche[cle] for cle in ("prenom", "nom", "telephone")} if fiche else {}
    try:
        CAISSE.reserver(etat, dict(identite, appareil=identifiant),
                        {"type": "enregistre"}, ev["t"])
    except caisse.REFUS as refus:
        etat["client"] = "Client autorisé hors ligne"
        note("RÉSERVATION HORS LIGNE sans caution : %s" % refus)
    client_sessions[identifiant] = etat
    reservations[balise] = identifiant
    planches[balise]["statut"] = "réservée"
    note("RÉSERVATION HORS LIGNE %s → %s" % (etat["client"], balise))

# --- Traitement centralisé des événements reçus par les stations ---
def traiter(ev):
    global horloge
    t, type_, station = ev.get("t"), ev.get("evenement"), ev.get("station")
    if not isinstance(t, (int, float)) or not 0 <= t < 4e9:
        return note("événement sans heure valide ignoré : %r" % (ev,))
    if t < signes.get(station, t) - 60:
        horloge = t  # simulateur relancé : son horloge repart de 9 h
    horloge = max(horloge, t)
    if station not in signes: note("station %s branchée" % station)
    signes[station] = t
    dernier_contact[station] = time.monotonic()

    # Les TIC font avancer les délais : réservations expirées, 23 h.
    if type_ == "TIC":
        retards(); return

    # Une réservation hors ligne est validée sans accès réseau, on la réplique dans l'état du cloud.
    if type_ == "OFFLINE_RESERVATION":
        return reserver_hors_ligne(ev, station)
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
            experience.update({"etat": "en cours", "reservation_status": "Consommée", "depart_a": ev["t"]})
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
                    experience.update({"etat": "en cours", "reservation_status": "Consommée", "depart_a": ev["t"]})
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
        sessions[balise] = {"client": client, "debut": ev["t"], "identifiant": identifiant,
                            "limite": horaires.limite_retour(ev["t"])}
        note("DÉPART %s depuis %s — %s" % (balise, station, client))
        if experience and "autorisation" in experience:
            CAISSE.partir(experience, ev["t"])
    elif type_ == "RETOUR":
        note("RETOUR %s en %s" % (balise, station))
        cloturer(balise, station, ev["t"], False)
    elif type_ == "ETRANGERE":
        # Constat physique dans une autre station : on signale le besoin de
        # rééquilibrage sans clôturer arbitrairement une location active.
        note("ETRANGERE %s en %s" % (balise, station))
        event_id = ev.get("event_id") or hashlib.sha256("\0".join(str(ev.get(k, "")) for k in ("station", "evenement", "balise", "t")).encode("utf-8")).hexdigest()
        signaler_etrangere(balise, station, ev["t"], event_id)

def client_json(identifiant):
    e = client_sessions.get(identifiant)
    commun = {"t": horloge, "heure": horaires.texte_heure(horloge),
              "generation": demo_generation}

    if not e:
        e = photo_tasks.session_snapshot(identifiant, demo_generation)
    if not e:
        return dict(commun, etat="inconnue")

    r = {cle: valeur for cle, valeur in e.items()
         if cle not in ("autorisation", "identite")}
    r.update(CAISSE.resume(e) if "autorisation" in e
             else dict(SANS_PAIEMENT, messages=e.get("messages", [])))
    r.update(commun)

    if r["etat"] == "caution débitée":
        r["montant"] = r["debite"]
    if r["etat"] == "en cours":
        r["duree"] = max(0, horloge - r["depart_a"])
        r["montant"] = r["duree"] / 60 * TARIF_MIN
    if r["etat"] == "armée":
        fin = e["armee_a"] + horaires.DELAI_RESERVATION
        r["reservation_restante"] = max(0, fin - horloge)

    if r["etat"] == "retournée":
        tache = photo_tasks.latest_for_client(
            identifiant, r.get("session_id"), demo_generation
        )
        if tache:
            r["photo_task"] = tache

    return r

def tableau():
    lignes = ["STATIONS", "--------"]
    lignes += ["%s   %s / dernière nouvelle t=%.0f s" %
               (st, "Connectée" if time.monotonic() - dernier_contact.get(st, 0) < 5 else "En retard", t)
               for st, t in sorted(signes.items())] or ["Aucune station branchée"]
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
    timeout = 5  # coupe une connexion ouverte qui n'envoie rien
    def do_POST(self):
        with VERROU: self.traiter_post()
    def do_GET(self):
        with VERROU: self.traiter_get()
    def repondre(self, corps, type_="text/plain; charset=utf-8", code=200):
        data = corps.encode("utf-8") if isinstance(corps, str) else corps
        self.send_response(code); self.send_header("Content-Type", type_)
        self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)
    def json(self, objet, code=200):
        self.repondre(json.dumps(objet, ensure_ascii=False), "application/json; charset=utf-8", code)
    def lire_json(self):
        try: d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
        except (ValueError, UnicodeDecodeError): return None
        return d if isinstance(d, dict) else None
    def repondre_client(self, identifiant, erreur):
        """409 avec la phrase d'erreur, ou 200 avec l'état du client."""
        if erreur:
            return self.json({"erreur": erreur, "generation": demo_generation}, 409)
        return self.json(client_json(identifiant))
    def verifier_photo(self):
        """Valide la session et met durablement sa photo dans la file SQLite."""
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            return self.json({"erreur": "Envoi invalide. Réessayez avec une photo."}, 415)
        try:
            taille = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.json({"erreur": "Taille de fichier invalide."}, 400)
        if taille < 1:
            return self.json({"erreur": "Aucune photo reçue."}, 400)
        if taille > photo.MAX_REQUEST_BYTES:
            return self.json({"erreur": "La photo dépasse 8 Mo. Choisissez une image plus légère."}, 413)
        try:
            donnees = json.loads(self.rfile.read(taille).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self.json({"erreur": "L'envoi de la photo est illisible. Réessayez."}, 400)
        if not isinstance(donnees, dict):
            return self.json({"erreur": "Envoi de photo invalide."}, 400)

        identifiant, session_id = donnees.get("identifiant"), donnees.get("session_id")
        if not isinstance(identifiant, str) or not isinstance(session_id, str):
            return self.json({"erreur": "Session de location manquante."}, 400)

        task_id = donnees.get("task_id")
        if not isinstance(task_id, str):
            return self.json({"erreur": "Identifiant de tâche manquant. Réessayez l'envoi."}, 400)

        with state_lock:
            experience = client_sessions.get(identifiant)
            if not experience:
                experience = photo_tasks.session_snapshot(identifiant, demo_generation)
            if (not experience or experience.get("etat") != "retournée" or
                    experience.get("session_id") != session_id or
                    donnees.get("generation") != demo_generation):
                return self.json({"erreur": "Cette session n'est plus disponible. Actualisez la page."}, 409)
            planche_attendue = experience["balise"]
            generation = demo_generation

        try:
            mime, image = photo.decoder_image(donnees.get("image"))
            with state_lock:
                current = client_sessions.get(identifiant)
                if not current:
                    current = photo_tasks.session_snapshot(identifiant, demo_generation)
                if (demo_generation != generation or not current or
                        current.get("etat") != "retournée" or
                        current.get("session_id") != session_id or
                        current.get("balise") != planche_attendue):
                    return self.json({"erreur": "La session a changé pendant l'envoi. Actualisez la page."}, 409)

                tache, nouvelle = photo_tasks.enqueue(
                    task_id=task_id,
                    client_id=identifiant,
                    session_id=session_id,
                    board=planche_attendue,
                    station=current.get("retour_station", current.get("station", "A")),
                    generation=generation,
                    retour_t=current.get("retour_a", horloge),
                    duree=current.get("duree", 0),
                    montant=current.get("montant", 0),
                    mime=mime,
                    image=image,
                )
        except file_photos.FileTacheErreur as erreur:
            return self.json({"erreur": str(erreur)}, erreur.code)
        except photo.PhotoErreur as erreur:
            return self.json({"erreur": str(erreur)}, erreur.code)
        except Exception as erreur:
            photo_note("PHOTO %s : mise en file impossible (%s)" %
                       (planche_attendue, type(erreur).__name__))
            return self.json({"erreur": "Le cloud n'a pas pu enregistrer la photo. Réessayez."}, 500)

        if nouvelle:
            photo_note("PHOTO %s : reçue, analyse en attente" % planche_attendue)

        return self.json({
            "tache": tache,
            "generation": generation,
            "message": "Photo reçue. Analyse en attente."
        }, 202)

    # --- API HTTP du cloud ---
    def traiter_post(self):
        u = urlparse(self.path)
        if u.path == "/api/photo-verification":
            return self.verifier_photo()
        if u.path == "/api/condition":
            d = self.lire_json()
            if not d or not d.get("identifiant") or not d.get("session_id"):
                return self.json({"erreur": "Session manquante."}, 400)
            photo = d.get("photo")
            if photo is not None and not isinstance(photo, str):
                return self.json({"erreur": "Photo invalide."}, 400)
            rapport, erreur = enregistrer_rapport(str(d["identifiant"]), str(d["session_id"]),
                                                    d.get("condition"), photo)
            sauvegarder()
            return self.json({"erreur": erreur, "generation": demo_generation} if erreur else
                             {"rapport": rapport, "generation": demo_generation}, 409 if erreur else 200)
        if u.path == "/api/reparer":
            d = self.lire_json()
            if not d or not d.get("balise"):
                return self.json({"erreur": "Planche manquante."}, 400)
            statut, erreur = reparer(str(d["balise"]))
            sauvegarder()
            return self.json({"erreur": erreur} if erreur else {"statut": statut}, 409 if erreur else 200)
        if u.path == "/api/rebalancer":
            d = self.lire_json()
            if not d or not d.get("balise") or str(d["balise"]) not in planches:
                return self.json({"erreur": "Planche invalide."}, 400)
            statut, erreur = confirmer_rebalancement(str(d["balise"]))
            sauvegarder()
            return self.json({"erreur": erreur} if erreur else {"statut": statut}, 409 if erreur else 200)
        if u.path == "/api/admin/reset-demo":
            d = self.lire_json()
            if not d or d.get("confirmation") != "REINITIALISER":
                return self.json({"erreur": "Confirmation requise."}, 400)
            reinitialiser_demo()
            return self.json({"message": "Cloud réinitialisé.", "generation": demo_generation})
        if u.path == "/api/arme":
            d = self.lire_json()
            if (not d or not d.get("identifiant")
                    or not isinstance(d.get("client"), dict)
                    or not isinstance(d.get("moyen"), dict)):
                return self.json(
                    {"erreur": "Identité ou moyen de paiement manquant."}, 400)
            if "generation" in d and d["generation"] != demo_generation:
                return self.json({"erreur": "La démonstration a été réinitialisée.",
                                  "generation": demo_generation}, 409)
            identifiant, station = str(d["identifiant"]), str(d.get("station", "A"))
            _, erreur = armer(d["client"], d["moyen"], station, identifiant)
            sauvegarder()
            if erreur:
                return self.repondre_client(identifiant, erreur)
            etat = client_json(identifiant)
            etat["offline_authorization"] = autorisation_hors_ligne(identifiant, station)
            return self.json(etat)
        if u.path == "/api/annuler":
            d = self.lire_json()
            if not d or not d.get("identifiant"):
                return self.json({"erreur": "Session manquante."}, 400)
            identifiant = str(d["identifiant"])
            erreur = annuler(identifiant)
            sauvegarder()
            return self.repondre_client(identifiant, erreur)
        if u.path == "/evenements":
            brut = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
            try:
                events = [json.loads(ligne) for ligne in brut.strip().splitlines() if ligne.strip()]
                if not events:
                    return self.json({"erreur": "Événement manquant."}, 400)
                for ev in events:
                    if not isinstance(ev, dict):
                        return self.json({"erreur": "Événement invalide."}, 400)
                    if ev.get("evenement") == "TIC":
                        traiter(ev)
                        sauvegarder()
                        continue
                    event_id = ev.get("event_id") or hashlib.sha256(
                        "\0".join(str(ev.get(k, "")) for k in
                                  ("station", "evenement", "balise", "t")).encode("utf-8")
                    ).hexdigest()
                    if event_id in evenements_recus:
                        continue
                    traiter(ev)
                    evenements_recus.add(event_id)
                    sauvegarder()
            except (ValueError, UnicodeDecodeError) as e:
                return self.json({"erreur": "Événement illisible: %s" % e}, 400)
            except Exception as e:
                return self.json({"erreur": "Traitement impossible: %s" % e}, 500)
            return self.json({"ok": True})
        self.repondre("Introuvable", code=404)
    def traiter_get(self):
        u, q = urlparse(self.path), parse_qs(urlparse(self.path).query)
        if u.path == "/api/capabilities":
            return self.json({"version": "photo-queue-1", "photo_verification": True})
        if u.path in ("/parc", "/api/parc"):
            return self.json({"t": horloge, "heure": horaires.texte_heure(horloge),
                              "ouvert": horaires.locations_ouvertes(horloge),
                              "generation": demo_generation, "planches": planches})
        if u.path == "/api/client": return self.json(client_json(q.get("identifiant", [""])[0]))
        if u.path == "/arme":
            client = q.get("client", ["+33600000000"])[0].strip(); client = client if client.startswith("+") else "+" + client
            station, balise = q.get("station", ["A"])[0], suggestion(q.get("station", ["A"])[0])
            if not balise: return self.repondre("Plus de planche libre en station %s." % station)
            file_attente.setdefault(station, []).append(client); note("%s arme (mode historique) en station %s" % (client, station))
            return self.repondre("Prends la planche %s." % balise)
        if u.path == "/admin":
            alertes = "".join(
                "<li class='foreign'><strong>%s</strong> — Station d'origine : <strong>%s</strong> · Détectée à : <strong>%s</strong> · Depuis t = %.0f s "
                "<button type='button' onclick=\"rebalancer('%s')\">Rééquilibrage effectué</button></li>" %
                (html.escape(e["balise"]), html.escape(e["origine"]), html.escape(e["station"]), e["t"], html.escape(e["balise"], quote=True))
                for e in rebalancements_actifs.values()) or "<li>Aucune planche à rééquilibrer</li>"
            rapports = "".join("<li class='%s'>%s | %s | retour t=%.0f%s</li>" %
                               ("damaged" if r["condition"] == "DAMAGED" else "", html.escape(r["balise"]),
                                html.escape(r["condition"]), r["t"],
                                " | photo prototype: " + html.escape(r["photo"]) if r.get("photo") else "")
                               for r in rapports_planches[:10]) or "<li>Aucun retour reçu</li>"
            items = []
            for b, p in sorted(planches.items()):
                latest = next((r for r in rapports_planches if r["balise"] == b), None)
                reservation = next((e for e in reversed(list(client_sessions.values())) if e.get("balise") == b and e.get("reservation_status") in ("Réservée", "En cours", "Expirée")), None)
                if p["statut"] == "A_REEQUILIBRER":
                    status = "À rééquilibrer — détectée à %s (base %s)" % (p.get("ou"), p["origine"])
                elif p["statut"] == "maintenance":
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
            fiches = "".join("<li>%s</li>" % html.escape(ligne) for ligne in CAISSE.fiches()) or "<li>Aucun client</li>"
            page = "<!doctype html><meta charset=utf-8><title>KORKO admin</title><body style='font:14px ui-monospace,monospace;background:#ede3ce;color:#164b55;padding:24px'><h2>KORKO · administration · %s · t = %.0f s</h2><section class='foreign'><h3>À RÉÉQUILIBRER</h3><ul style='padding-left:20px;line-height:1.8'>%s</ul></section><section><h3>ÉTAT DES PLANCHES</h3><ul style='padding-left:20px;line-height:1.8'>%s</ul></section><section><h3>FICHES CLIENTS</h3><ul style='padding-left:20px;line-height:1.8'>%s</ul></section><section><h3>RAPPORTS DE CONDITION</h3><ul style='padding-left:20px;line-height:1.8'>%s</ul></section><pre>%s</pre><section style='margin-top:32px;padding:18px;border:2px solid #bd744c;border-radius:12px;background:#fffaf0'><h3>DÉMO</h3><button id='reset-start' type='button'>Réinitialiser la démo</button><div id='reset-confirm' hidden><p>Réinitialiser toutes les données temporaires de la démo ?</p><p>Les locations, réservations et états temporaires seront effacés.</p><button id='reset-cancel' type='button'>Annuler</button> <button id='reset-submit' type='button'>Réinitialiser</button></div><p>Efface les locations, réservations, sessions clients, files d'attente, rapports de condition, alertes/journal, compteurs de sorties et signaux de stations. Les états de maintenance et d'inspection sont remis à zéro. Les cautions encore bloquées sont libérées. Aucun fichier ni simulateur physique n'est modifié.</p><p id='reset-message' role='status' aria-live='polite'></p></section><style>.damaged{color:#a13225;font-weight:bold;background:#f4d8cf}.foreign{padding:12px 16px;border:2px solid #a13225;border-radius:10px;background:#f7ded7;color:#76251c}button{padding:10px 14px;cursor:pointer}</style><script>async function reparer(b){const r=await fetch('/api/reparer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({balise:b})});const d=await r.json();if(!r.ok)alert(d.erreur);else location.reload()}async function rebalancer(b){const r=await fetch('/api/rebalancer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({balise:b})});const d=await r.json();if(!r.ok)alert(d.erreur);else location.reload()}const start=document.getElementById('reset-start'),box=document.getElementById('reset-confirm'),msg=document.getElementById('reset-message');start.onclick=()=>{box.hidden=false;start.disabled=true};document.getElementById('reset-cancel').onclick=()=>{box.hidden=true;start.disabled=false};document.getElementById('reset-submit').onclick=async()=>{const b=document.getElementById('reset-submit');b.disabled=true;try{const r=await fetch('/api/admin/reset-demo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation:'REINITIALISER'})});const d=await r.json();if(!r.ok)throw Error(d.erreur);sessionStorage.setItem('korko-reset-message',JSON.stringify({message:d.message,until:Date.now()+10000}));location.reload()}catch(e){msg.textContent=e.message;b.disabled=false}};const saved=JSON.parse(sessionStorage.getItem('korko-reset-message')||'null');if(saved&&saved.until>Date.now())msg.textContent=saved.message;else sessionStorage.removeItem('korko-reset-message');function refresh(){setTimeout(()=>{if(box.hidden)location.reload();else refresh()},2000)}refresh();</script></body>" % (horaires.texte_heure(horloge), horloge, alertes, "".join(items), fiches, rapports, html.escape(tableau()))
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
    # sous Windows, une sortie redirigée vers un fichier refuse « → » et les émojis
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    charger()
    photo_tasks.set_demo_generation(demo_generation)
    photo_tasks.start(on_log=photo_note)
    PUBLIEUR.start()
    print("Cloud KORKO sur http://0.0.0.0:%d" % PORT)
    print("  registre : %s" % chaine.lien_adresse(REGISTRE.address))
    print("  expérience client : /     administration : /admin     parc brut : /parc")
    print("  stations : POST /evenements\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), Cloud).serve_forever()
