"""
crypto.py — la caution crypto, bloquée dans un séquestre sur Fuji.

Le client qui paie en crypto reçoit un portefeuille de démo, approvisionné en
AVAX de test par le compte du cloud : aucun argent réel. À la réservation, ce
portefeuille bloque la caution dans le contrat SequestreKorko. Au retour,
KORKO y prend le prix de la location et le contrat rend le reste au client ;
à 23 h sans retour, KORKO saisit toute la caution.

Les transactions partent en tâche de fond, dans l'ordre : une location
n'attend jamais la blockchain. L'adresse du contrat est lue dans
sequestre_korko.json, écrit par deployer_sequestre.py.
"""

import json
import os
import queue
import threading
import time

from eth_account import Account
from hexbytes import HexBytes
from web3.exceptions import TimeExhausted, TransactionNotFound

from smart_contract import chaine

TAUX_DEMO = 10**12      # wei d'AVAX de test par euro : 1 € = 0,000001 AVAX
DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_SEQUESTRE = os.path.join(DOSSIER, "sequestre_korko.json")
GAZ_BLOQUER = 100_000   # limite de gaz du blocage (≈ 68 000 consommés)
GAZ_VIREMENT = 21_000   # gaz d'un simple virement d'AVAX
GAZ_REGLEMENT = 100_000 # limite de gaz pour cloturer/saisir une caution

#: le virement d'approvisionnement reprend le réseau et les frais du blocage
CHAMPS_REPRIS = ("chainId", "maxFeePerGas", "maxPriorityFeePerGas")

#: Fuji injoignable, lent à confirmer, ou qui refuse l'envoi pour l'instant
#: (nonce pris par une autre transaction…) : l'étape en cours est réessayée
ATTENTES = (TimeExhausted, *chaine.ERREURS_RESEAU)


class FondsInsuffisants(Exception):
    """Le compte ne peut pas payer la transaction : inutile d'insister."""


def creer_portefeuille():
    """Crée le portefeuille de démo d'un client : adresse et clé privée."""
    compte = Account.create()
    return {"adresse": compte.address, "cle": compte.key.to_0x_hex()}


def en_wei(euros):
    """Convertit des euros en wei d'AVAX de test, au taux de démo."""
    return round(euros * TAUX_DEMO)


def abreger(identifiant):
    """« 0x1a2b3c…9f0e » : le début et la fin d'une adresse ou d'un hash."""
    return "%s…%s" % (identifiant[:8], identifiant[-4:])


def ouvrir_sequestre(w3):
    """Retourne le contrat déployé, décrit par sequestre_korko.json."""
    with open(FICHIER_SEQUESTRE, encoding="utf-8") as fichier:
        deploiement = json.load(fichier)
    return w3.eth.contract(address=deploiement["adresse"],
                           abi=deploiement["abi"], decode_tuples=True)


def demarrer(journaliser):
    """Lance le fil du séquestre sur Fuji et le retourne.

    Retourne None tant que le contrat n'est pas déployé.
    """
    if not os.path.exists(FICHIER_SEQUESTRE):
        return None
    contrat = ouvrir_sequestre(chaine.connecter_fuji())
    sequestre = Sequestre(contrat, chaine.compte_du_cloud(), journaliser)
    sequestre.start()
    return sequestre


class Envoi:
    """Une transaction signée, renvoyée à l'identique à chaque essai.

    Si la réponse du nœud se perd, l'essai suivant renvoie la même
    transaction signée : elle ne peut pas être exécutée deux fois. Elle
    n'est signée à nouveau que si un autre envoi du compte a pris son nonce.
    """

    def __init__(self, w3, compte, transaction):
        self.w3 = w3
        self.compte = compte
        self.transaction = transaction
        self.nonce, self.signee = None, None

    def confirmer(self):
        """Envoie la transaction, attend sa confirmation, retourne son hash.

        Lève une des ATTENTES s'il faut réessayer, FondsInsuffisants si le
        compte ne peut pas la payer, TransactionAnnulee si elle a été
        minée puis annulée.
        """
        if self.signee is None:
            self.signer()
        try:
            self.w3.eth.send_raw_transaction(self.signee.raw_transaction)
        except chaine.ERREURS_RESEAU:
            if not self.deja_envoyee():
                self.verifier_fonds()
                raise
        chaine.attendre(self.w3, self.signee.hash)
        return self.signee.hash

    def signer(self):
        """Signe la transaction avec le prochain nonce libre du compte."""
        adresse = self.compte.address
        self.nonce = self.w3.eth.get_transaction_count(adresse, "pending")
        transaction = dict(self.transaction, nonce=self.nonce)
        self.signee = self.compte.sign_transaction(transaction)

    def deja_envoyee(self):
        """Vrai si le nœud connaît déjà la transaction signée.

        Si une autre transaction du même compte a pris son nonce, celle-ci
        ne sera jamais minée : on l'oublie pour la signer à nouveau.
        """
        try:
            self.w3.eth.get_transaction(self.signee.hash)
            return True
        except TransactionNotFound:
            adresse = self.compte.address
            if self.nonce < self.w3.eth.get_transaction_count(adresse):
                self.signee = None
            return False

    def verifier_fonds(self):
        """Lève FondsInsuffisants si le compte ne peut pas payer l'envoi.

        Même règle que le nœud : valeur + gaz × frais maximum.
        """
        transaction = self.transaction
        cout = (transaction.get("value", 0)
                + transaction["gas"] * transaction["maxFeePerGas"])
        if self.w3.eth.get_balance(self.compte.address) < cout:
            raise FondsInsuffisants("fonds insuffisants sur %s"
                                    % abreger(self.compte.address))


class Sequestre(threading.Thread):
    """Bloque puis règle les cautions crypto, une par une, dans l'ordre.

    Tant que Fuji est injoignable ou lent, l'étape en cours est réessayée.
    Une opération impossible (refus du contrat, fonds insuffisants,
    transaction annulée) passe l'autorisation en « échec » sans arrêter
    la file.
    """

    def __init__(self, contrat, compte_korko, journaliser):
        super().__init__(daemon=True)
        self.contrat = contrat
        self.w3 = contrat.w3
        self.korko = compte_korko
        self.journaliser = journaliser
        self.file = queue.Queue()
        self.dernier_incident = None

    def bloquer(self, autorisation, cle_client):
        """Met en file le blocage de la caution par le portefeuille client."""
        self.file.put(("blocage", self.executer_blocage, autorisation,
                       cle_client))

    def debiter(self, autorisation, montant):
        """Met en file le paiement de `montant` euros sur la caution."""
        self.file.put(("règlement", self.executer_reglement, autorisation,
                       montant))

    def run(self):
        """Traite la file dans l'ordre d'arrivée ; un échec ne l'arrête pas."""
        while True:
            etape, operation, autorisation, argument = self.file.get()
            try:
                operation(autorisation, argument)
            except Exception as erreur:
                self.noter_echec(etape, autorisation, erreur)
            self.file.task_done()

    def noter_echec(self, etape, autorisation, erreur):
        """Passe l'autorisation en « échec » et journalise la raison.

        Une transaction minée puis annulée est une preuve : son lien est
        ajouté à l'autorisation.
        """
        if isinstance(erreur, chaine.TransactionAnnulee):
            raison = chaine.lien(HexBytes(str(erreur)))
            autorisation.liens.append(raison)
        else:
            raison = chaine.nommer_refus(self.contrat, erreur)
        autorisation.etat = "échec"
        self.journaliser("CRYPTO échec du %s %s : %s"
                         % (etape, abreger(autorisation.reference), raison))

    def executer_blocage(self, autorisation, cle_client):
        """Bloque la caution depuis le portefeuille du client.

        Le compte du cloud l'approvisionne d'abord, si besoin.
        """
        client = Account.from_key(cle_client)
        valeur = en_wei(autorisation.montant)
        appel = self.contrat.functions.bloquer(autorisation.reference)
        options = {"from": client.address, "value": valeur,
                   "gas": GAZ_BLOQUER}
        transaction = self.insister(appel.build_transaction, options)
        frais_max = GAZ_BLOQUER * transaction["maxFeePerGas"]
        self.approvisionner(transaction, valeur + frais_max)
        hash_transaction = self.transmettre(client, transaction)
        self.noter(autorisation, "bloquée", hash_transaction)

    def approvisionner(self, transaction, besoin):
        """Complète le portefeuille qui signera `transaction` jusqu'à `besoin`.

        Le compte du cloud y vire ce qui manque, en wei, aux mêmes frais.
        """
        adresse = transaction["from"]
        manque = besoin - self.insister(self.w3.eth.get_balance, adresse)
        if manque <= 0:
            return
        virement = {cle: transaction[cle] for cle in CHAMPS_REPRIS}
        virement.update(to=adresse, value=manque, gas=GAZ_VIREMENT)
        hash_transaction = self.transmettre(self.korko, virement)
        self.journaliser("CRYPTO approvisionnement %s : %s"
                         % (abreger(adresse), chaine.lien(hash_transaction)))

    def executer_reglement(self, autorisation, montant):
        """Paie la location sur la caution et rend le reste au client.

        Si `montant` (en euros) atteint la caution, KORKO la saisit entière.
        """
        fonctions = self.contrat.functions
        reference = autorisation.reference
        if montant >= autorisation.montant:
            appel, etat = fonctions.saisir(reference), "débitée"
        else:
            appel = fonctions.cloturer(reference, en_wei(montant))
            etat = "libérée"
        options = {"from": self.korko.address, "gas": GAZ_REGLEMENT}
        self.insister(appel.call, options)
        transaction = self.insister(appel.build_transaction, options)
        hash_transaction = self.transmettre(self.korko, transaction)
        autorisation.debite = montant
        self.noter(autorisation, etat, hash_transaction)

    def noter(self, autorisation, etat, hash_transaction):
        """Ajoute la preuve à l'autorisation, puis change son état."""
        preuve = chaine.lien(hash_transaction)
        autorisation.liens.append(preuve)
        autorisation.etat = etat
        self.journaliser("CRYPTO caution %s %s : %s"
                         % (etat, abreger(autorisation.reference), preuve))

    def transmettre(self, compte, transaction):
        """Envoie la transaction, signée une seule fois ; retourne son hash."""
        return self.insister(Envoi(self.w3, compte, transaction).confirmer)

    def insister(self, action, *arguments):
        """Retourne le résultat de l'action, réessayée sur une des ATTENTES."""
        while True:
            try:
                resultat = action(*arguments)
            except ATTENTES as erreur:
                self.signaler_incident("CRYPTO nouvel essai : %s" % erreur)
                time.sleep(chaine.PAUSE_RESEAU)
            else:
                self.dernier_incident = None
                return resultat

    def signaler_incident(self, message):
        """Journalise un incident une seule fois, pas à chaque essai."""
        if message != self.dernier_incident:
            self.journaliser(message)
        self.dernier_incident = message
