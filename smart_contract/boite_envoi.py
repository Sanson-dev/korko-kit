"""
boite_envoi.py — la boîte d'envoi persistante des inscriptions blockchain.

Chaque événement à inscrire est d'abord écrit dans la table
blockchain_outbox de la base SQLite du cloud, korko_cloud.sqlite3 (la même
que celle de l'état du cloud sur la branche de Sanson ; KORKO_CLOUD_DB pour
la déplacer). Il y reste « en attente » jusqu'à son inscription : une coupure
ou un redémarrage du cloud ne le perd pas.

Chaque événement est identifié par son event_id, calculé comme sur la
branche de Sanson : un événement renvoyé par une station n'est inscrit
qu'une fois. La transaction signée y est gardée aussi, pour être renvoyée à
l'identique après un redémarrage, jamais signée deux fois.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import closing

DOSSIER_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("KORKO_CLOUD_DB",
                      os.path.join(DOSSIER_PROJET, "korko_cloud.sqlite3"))

SCHEMA = """CREATE TABLE IF NOT EXISTS blockchain_outbox (
    rang INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    evenement TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'en attente',
    nonce INTEGER,
    brut TEXT,
    preuve TEXT)"""

COLONNES = "event_id, evenement, nonce, brut"


def identifiant(evenement):
    """Retourne l'event_id de l'événement, comme sur la branche de Sanson."""
    cles = ("station", "evenement", "balise", "t")
    texte = "\0".join(str(evenement.get(cle, "")) for cle in cles)
    return (evenement.get("event_id")
            or hashlib.sha256(texte.encode("utf-8")).hexdigest())


def envoi(ligne):
    """Retourne un envoi : l'événement, et sa signature s'il en a une."""
    event_id, evenement, nonce, brut = ligne
    return {"event_id": event_id, "evenement": json.loads(evenement),
            "nonce": nonce, "brut": brut}


class BoiteEnvoi:
    """La table blockchain_outbox : ce qui reste à inscrire, dans l'ordre."""

    def __init__(self, chemin=BASE):
        self.chemin = chemin
        self.executer(SCHEMA)

    def executer(self, requete, parametres=()):
        """Exécute la requête dans sa propre connexion (chaque fil ouvre la
        sienne) ; retourne le nombre de lignes touchées et les lignes lues."""
        with closing(sqlite3.connect(self.chemin, timeout=10)) as base:
            with base:
                curseur = base.execute(requete, parametres)
                return curseur.rowcount, curseur.fetchall()

    def deposer(self, evenement):
        """Garde l'événement ; retourne son envoi, ou None s'il est connu."""
        ligne = (identifiant(evenement),
                 json.dumps(evenement, ensure_ascii=False))
        ajoutes, _ = self.executer(
            "INSERT OR IGNORE INTO blockchain_outbox(event_id, evenement) "
            "VALUES (?, ?)", ligne)
        return envoi(ligne + (None, None)) if ajoutes else None

    def en_attente(self):
        """Retourne les envois pas encore inscrits, dans l'ordre d'arrivée."""
        _, lignes = self.executer(
            "SELECT %s FROM blockchain_outbox WHERE statut = 'en attente' "
            "ORDER BY rang" % COLONNES)
        return [envoi(ligne) for ligne in lignes]

    def signer(self, event_id, nonce, brut):
        """Garde la transaction signée (ou l'oublie, avec None)."""
        self.executer("UPDATE blockchain_outbox SET nonce = ?, brut = ? "
                      "WHERE event_id = ?", (nonce, brut, event_id))

    def clore(self, event_id, statut, preuve):
        """Sort l'événement de l'attente : inscrit, refusé, ignoré…"""
        self.executer("UPDATE blockchain_outbox SET statut = ?, preuve = ? "
                      "WHERE event_id = ?", (statut, preuve, event_id))
