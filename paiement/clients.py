"""
clients.py — les fiches clients : identité et moyen de paiement enregistré.

Une fiche par numéro de téléphone, gardée dans clients.json (jamais
versionné : ce sont des données personnelles). On n'y garde jamais de
numéro de carte, seulement sa marque et ses 4 derniers chiffres, comme chez
un vrai prestataire de paiement.
"""

import json
import os
import re

from paiement import crypto

DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_CLIENTS = os.path.join(DOSSIER, "clients.json")
LONGUEUR_NOM = 40


class ErreurFiche(ValueError):
    """Les informations reçues ne permettent pas d'établir la fiche."""


def normaliser_telephone(brut):
    """« 06 12 34 56 78 » devient « +33612345678 »."""
    chiffres = re.sub(r"[^0-9+]", "", str(brut))
    if re.fullmatch(r"0[1-9][0-9]{8}", chiffres):
        return "+33" + chiffres[1:]
    if re.fullmatch(r"\+[0-9]{8,15}", chiffres):
        return chiffres
    raise ErreurFiche("Numéro de téléphone invalide.")


def nettoyer_nom(brut, champ):
    """Retourne le prénom ou le nom sans espaces superflus, non vide."""
    nom = " ".join(str(brut or "").split())[:LONGUEUR_NOM]
    if not nom:
        raise ErreurFiche("Le %s est obligatoire." % champ)
    return nom


def carte(recu):
    """Retourne la carte à enregistrer : marque et 4 derniers chiffres."""
    marque = re.sub(r"[^A-Za-z ]", "", str(recu.get("marque", ""))).strip()
    derniers4 = str(recu.get("derniers4", ""))
    expiration = str(recu.get("expiration", ""))
    if not re.fullmatch(r"[0-9]{4}", derniers4) or not re.fullmatch(
            r"(0[1-9]|1[0-2])/[0-9]{2}", expiration):
        raise ErreurFiche("Carte incomplète.")
    marque = marque[:20] or "Carte"
    return {"type": "carte", "libelle": "%s •••• %s" % (marque, derniers4),
            "derniers4": derniers4, "expiration": expiration}


class Fichier:
    """Les fiches clients, indexées par numéro de téléphone."""

    def __init__(self, chemin=FICHIER_CLIENTS):
        self.chemin = chemin
        self.fiches = {}
        if os.path.exists(chemin):
            with open(chemin, encoding="utf-8") as fichier:
                self.fiches = json.load(fichier)

    def enregistrer(self, client, moyen):
        """Crée ou met à jour la fiche du client, puis la retourne."""
        telephone = normaliser_telephone(client.get("telephone", ""))
        fiche = self.fiches.get(telephone, {"locations": []})
        fiche["prenom"] = nettoyer_nom(client.get("prenom"), "prénom")
        fiche["nom"] = nettoyer_nom(client.get("nom"), "nom")
        fiche["telephone"] = telephone
        fiche["moyen"] = self.moyen_de_paiement(fiche, moyen)
        self.fiches[telephone] = fiche
        self.sauvegarder()
        return fiche

    def moyen_de_paiement(self, fiche, recu):
        """Retourne le moyen choisi ; « enregistre » reprend l'ancien."""
        type_moyen = recu.get("type")
        if type_moyen == "enregistre" and fiche.get("moyen"):
            return fiche["moyen"]
        if type_moyen == "enregistre":
            raise ErreurFiche("Aucun moyen de paiement enregistré.")
        if type_moyen == "carte":
            return carte(recu)
        if type_moyen == "apple_pay":
            return {"type": "apple_pay", "libelle": "Apple Pay"}
        if type_moyen == "crypto":
            return self.portefeuille(fiche)
        raise ErreurFiche("Moyen de paiement inconnu.")

    def portefeuille(self, fiche):
        """Garde le portefeuille crypto de démo du client, ou en crée un."""
        if "portefeuille" not in fiche:
            fiche["portefeuille"] = crypto.creer_portefeuille()
        adresse = fiche["portefeuille"]["adresse"]
        libelle = "Crypto %s…%s (Fuji)" % (adresse[:6], adresse[-4:])
        return {"type": "crypto", "libelle": libelle}

    def ajouter_location(self, telephone, location):
        """Ajoute une location terminée à l'historique du client."""
        self.fiches[telephone]["locations"].append(location)
        self.sauvegarder()

    def lister(self):
        """Retourne les fiches, triées par nom puis prénom."""
        return sorted(self.fiches.values(),
                      key=lambda fiche: (fiche["nom"], fiche["prenom"]))

    def sauvegarder(self):
        """Écrit toutes les fiches, d'un seul coup, dans clients.json."""
        provisoire = self.chemin + ".tmp"
        with open(provisoire, "w", encoding="utf-8") as fichier:
            json.dump(self.fiches, fichier, ensure_ascii=False, indent=1)
        os.replace(provisoire, self.chemin)
