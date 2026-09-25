"""
caisse.py — le paiement d'une location, de la réservation au retour.

À la réservation : fiche client, caution bloquée, message de bienvenue.
Au retour : prix de la location débité, caution libérée.
À 23 h sans retour : caution débitée.
Les messages sont des SMS simulés, affichés dans l'appli du client.
"""

from paiement import clients, crypto, horaires, prestataire

#: les passages entre ** s'affichent en gras dans l'appli
BIENVENUE = ("🏄 Bonne session de surf, amusez-vous bien ! ☀️🌊 Pour rappel, "
             "**vous devez nous rendre la planche avant 23 heures**, sinon la "
             "caution vous sera débitée.")

#: refus à expliquer au client, sans rien réserver
REFUS = (clients.ErreurFiche, prestataire.RefusDePaiement)


def euros(montant):
    """1.5 devient « 1,50 € »."""
    return ("%.2f €" % montant).replace(".", ",")


def creer_caisse(journaliser):
    """Retourne la caisse du cloud : fiches, prestataire et séquestre."""
    sequestre = crypto.demarrer(journaliser)
    return Caisse(clients.Fichier(),
                  prestataire.Prestataire(sequestre, journaliser), journaliser)


class Caisse:
    """Relie chaque location à la fiche du client et à sa caution."""

    def __init__(self, fichier, prestataire_paiement, journaliser):
        self.fichier = fichier
        self.prestataire = prestataire_paiement
        self.journaliser = journaliser

    def reserver(self, location, client, moyen, t):
        """Identifie le client et bloque sa caution ; lève REFUS."""
        fiche = self.fichier.enregistrer(client, moyen)
        autorisation = self.prestataire.bloquer(fiche)
        location.update({
            "client": "%s %s" % (fiche["prenom"], fiche["nom"]),
            "identite": {cle: fiche[cle]
                         for cle in ("prenom", "nom", "telephone")},
            "autorisation": autorisation, "messages": []})
        self.envoyer(location, BIENVENUE, t)

    def terminer(self, location, montant, t):
        """Débite le prix de la location et libère la caution."""
        autorisation = location["autorisation"]
        self.prestataire.debiter(autorisation, montant)
        self.archiver(location, montant, t)
        self.envoyer(location, "Merci %s ! 🏄 %s débités sur %s. Votre caution "
                     "de %s est libérée. À la prochaine fois ! 🌊"
                     % (location["identite"]["prenom"], euros(montant),
                        autorisation.libelle, euros(autorisation.montant)), t)

    def annuler(self, location, t):
        """Le client renonce avant de partir : rien n'est débité."""
        self.prestataire.debiter(location["autorisation"], 0.0)
        self.envoyer(location, "Réservation annulée, %s : rien n'a été débité "
                     "et votre caution est libérée. À bientôt ! 🌊"
                     % location["identite"]["prenom"], t)

    def saisir_caution(self, location, t):
        """La planche n'est pas revenue avant 23 h : la caution est débitée."""
        autorisation = location["autorisation"]
        self.prestataire.debiter(autorisation, autorisation.montant)
        self.archiver(location, autorisation.montant, t)
        self.envoyer(location, "La planche %s n'a pas été rendue avant 23 h : "
                     "votre caution de %s a été débitée."
                     % (location["balise"], euros(autorisation.montant)), t)

    def archiver(self, location, montant, t):
        """Ajoute la location terminée à la fiche du client."""
        self.fichier.ajouter_location(location["identite"]["telephone"], {
            "planche": location["balise"], "montant": round(montant, 2),
            "moyen": location["autorisation"].libelle, "t": t,
            "heure": horaires.texte_heure(t)})

    def envoyer(self, location, texte, t):
        """Envoie un SMS simulé : il s'affiche dans l'appli du client."""
        location["messages"].append({"heure": horaires.texte_heure(t),
                                     "texte": texte})
        self.journaliser("SMS → %s : %s" % (location["client"], texte))

    def fiches(self):
        """Retourne une ligne de résumé par client, pour l'administration."""
        lignes = []
        for fiche in self.fichier.lister():
            locations = fiche["locations"]
            total = sum(location["montant"] for location in locations)
            lignes.append("%s %s · %s · %s · %d location(s) · %s payés"
                          % (fiche["prenom"], fiche["nom"], fiche["telephone"],
                             fiche["moyen"]["libelle"], len(locations),
                             euros(total)))
        return lignes

    def resume(self, location):
        """Retourne ce que l'appli du client affiche de son paiement."""
        autorisation = location["autorisation"]
        return {
            "client": location["identite"],
            "paiement": {"type": autorisation.moyen,
                         "libelle": autorisation.libelle},
            "caution": {"montant": autorisation.montant,
                        "etat": autorisation.etat,
                        "liens": list(autorisation.liens)},
            "debite": autorisation.debite,
            "messages": location["messages"]}
