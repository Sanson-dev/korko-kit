"""
prestataire.py — le prestataire de paiement de KORKO.

Il fonctionne comme un vrai prestataire (Stripe…) : au départ, il BLOQUE la
caution sur le moyen de paiement du client, sans rien débiter ; à la fin,
il DÉBITE le prix de la location et libère le reste ; si la planche n'est
pas rendue à 23 h, il débite la caution entière.

La carte bancaire et Apple Pay sont simulés : aucun argent réel ne bouge.
La crypto est réelle, sur le réseau de test Fuji : voir crypto.py.
"""

import secrets

CAUTION = 300.0
FIN_CARTE_REFUSEE = "0002"  # comme la carte de test « refusée » de Stripe


def euros(montant):
    """1.5 devient « 1,50 € »."""
    return ("%.2f €" % montant).replace(".", ",")


class RefusDePaiement(Exception):
    """Le moyen de paiement ne permet pas de bloquer la caution."""


class Autorisation:
    """La caution bloquée pour une location, et ce qu'elle est devenue."""

    def __init__(self, reference, moyen, libelle):
        self.reference = reference
        self.moyen = moyen
        self.libelle = libelle
        self.montant = CAUTION
        self.etat = "en attente"
        self.debite = None
        self.liens = []

    @classmethod
    def depuis(cls, donnees):
        """Recrée une autorisation sauvegardée dans la base du cloud."""
        autorisation = cls(donnees["reference"], donnees["moyen"], donnees["libelle"])
        vars(autorisation).update(donnees)
        return autorisation


class Prestataire:
    """Bloque et débite les cautions ; la crypto passe par le séquestre."""

    def __init__(self, sequestre, journaliser):
        self.sequestre = sequestre  # crypto.Sequestre, ou None
        self.journaliser = journaliser

    def bloquer(self, fiche):
        """Bloque la caution ; lève RefusDePaiement si c'est impossible."""
        moyen = fiche["moyen"]
        if moyen["type"] == "crypto":
            return self.bloquer_en_crypto(fiche)
        if moyen.get("derniers4") == FIN_CARTE_REFUSEE:
            raise RefusDePaiement("Carte refusée par la banque (simulation).")
        reference = "auth_sim_" + secrets.token_hex(8)
        autorisation = Autorisation(reference, moyen["type"], moyen["libelle"])
        autorisation.etat = "bloquée"
        self.journaliser(
            "PAIEMENT caution de %s bloquée sur %s "
            "(simulation)" % (euros(CAUTION), moyen["libelle"])
        )
        return autorisation

    def bloquer_en_crypto(self, fiche):
        """Confie la caution au séquestre de Fuji ; le blocage suit."""
        if self.sequestre is None:
            raise RefusDePaiement(
                "Paiement crypto indisponible pour le "
                "moment : choisissez un autre moyen."
            )
        reference = "0x" + secrets.token_hex(32)
        autorisation = Autorisation(reference, "crypto", fiche["moyen"]["libelle"])
        self.sequestre.bloquer(autorisation, fiche["portefeuille"]["cle"])
        return autorisation

    def debiter(self, autorisation, montant):
        """Débite `montant` euros, au plus la caution, et libère le reste."""
        montant = min(round(montant, 2), autorisation.montant)
        if autorisation.moyen == "crypto":
            self.sequestre.debiter(autorisation, montant)
            return
        autorisation.debite = montant
        integral = montant >= autorisation.montant
        autorisation.etat = "débitée" if integral else "libérée"
        self.journaliser(
            "PAIEMENT %s débités sur %s%s (simulation)"
            % (
                euros(montant),
                autorisation.libelle,
                "" if integral else ", caution libérée",
            )
        )
