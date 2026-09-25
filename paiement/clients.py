"""
clients.py — les fiches clients : identité et moyen de paiement enregistré.

Une fiche par numéro de téléphone, gardée dans clients.json (jamais
versionné : ce sont des données personnelles). On n'y garde jamais de
numéro de carte, seulement sa marque, ses 4 derniers chiffres et sa date
d'expiration, comme chez un vrai prestataire de paiement ; en crypto, la clé
du portefeuille de démo (sans valeur réelle).

Le moyen enregistré ne resert que sur les téléphones du client : taper le
numéro de quelqu'un d'autre ne permet pas de payer avec sa carte.
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
    chiffres = re.sub(r"^(\+|00)330?", "0", chiffres)
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
    """Retourne la carte à enregistrer : libellé, 4 derniers chiffres,
    expiration."""
    marque = re.sub(r"[^A-Za-z ]", "", str(recu.get("marque", ""))).strip()
    derniers4 = str(recu.get("derniers4", ""))
    expiration = str(recu.get("expiration", ""))
    if not re.fullmatch(r"[0-9]{4}", derniers4) or not re.fullmatch(
        r"(0[1-9]|1[0-2])/[0-9]{2}", expiration
    ):
        raise ErreurFiche("Carte incomplète.")
    marque = marque[:20] or "Carte"
    return {
        "type": "carte",
        "libelle": "%s •••• %s" % (marque, derniers4),
        "derniers4": derniers4,
        "expiration": expiration,
    }


class Fichier:
    """Les fiches clients, indexées par numéro de téléphone."""

    def __init__(self, chemin=FICHIER_CLIENTS):
        self.chemin = chemin
        self.fiches = {}
        if os.path.exists(chemin):
            with open(chemin, encoding="utf-8") as fichier:
                self.fiches = json.load(fichier)

    def preparer(self, client, moyen):
        """Retourne la fiche du client mise à jour, sans l'enregistrer.

        `client` porte aussi l'identifiant de son téléphone (« appareil »).
        """
        telephone = normaliser_telephone(client.get("telephone", ""))
        fiche = dict(self.fiches.get(telephone, {"locations": []}))
        fiche["prenom"] = nettoyer_nom(client.get("prenom"), "prénom")
        fiche["nom"] = nettoyer_nom(client.get("nom"), "nom")
        fiche["telephone"] = telephone
        appareil = client.get("appareil")
        appareils = fiche.get("appareils", [])
        if moyen.get("type") == "enregistre" and appareil not in appareils:
            raise ErreurFiche(
                "Pour votre sécurité, choisissez à nouveau " "votre moyen de paiement."
            )
        fiche["moyen"] = self.moyen_de_paiement(fiche, moyen)
        if moyen.get("type") != "enregistre":
            # un nouveau moyen ne resert que depuis le téléphone qui l'a saisi
            fiche["appareils"] = [appareil]
        return fiche

    def retenir(self, fiche):
        """Enregistre la fiche : à faire une fois la caution bloquée."""
        self.fiches[fiche["telephone"]] = fiche
        self.sauvegarder()

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
            return self.portefeuille(fiche, bool(recu.get("nouveau")))
        raise ErreurFiche("Moyen de paiement inconnu.")

    def portefeuille(self, fiche, nouveau):
        """Retourne le moyen crypto ; crée d'abord un portefeuille de démo
        s'il manque, ou si le client en demande un nouveau (l'ancien est
        archivé dans la fiche, pour ne perdre aucune clé)."""
        if nouveau and "portefeuille" in fiche:
            ancien = fiche.pop("portefeuille")
            fiche["anciens_portefeuilles"] = fiche.get("anciens_portefeuilles", []) + [
                ancien
            ]
        if "portefeuille" not in fiche:
            fiche["portefeuille"] = crypto.creer_portefeuille()
        adresse = fiche["portefeuille"]["adresse"]
        libelle = "Crypto %s…%s (Fuji)" % (adresse[:6], adresse[-4:])
        return {"type": "crypto", "libelle": libelle}

    def par_appareil(self, appareil):
        """Retourne la fiche du client qui utilise ce téléphone, ou None."""
        return next(
            (
                fiche
                for fiche in self.fiches.values()
                if appareil in fiche.get("appareils", [])
            ),
            None,
        )

    def ajouter_location(self, telephone, location):
        """Ajoute une location terminée à l'historique du client."""
        self.fiches[telephone]["locations"].append(location)
        self.sauvegarder()

    def lister(self):
        """Retourne les fiches, triées par nom puis prénom."""
        return sorted(
            self.fiches.values(), key=lambda fiche: (fiche["nom"], fiche["prenom"])
        )

    def sauvegarder(self):
        """Écrit toutes les fiches, d'un seul coup, dans clients.json."""
        provisoire = self.chemin + ".tmp"
        with open(provisoire, "w", encoding="utf-8") as fichier:
            json.dump(self.fiches, fichier, ensure_ascii=False, indent=1)
        os.replace(provisoire, self.chemin)
