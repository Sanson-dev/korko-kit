"""
chaine.py — le lien entre le cloud KORKO et la blockchain Avalanche.

Chaque départ et chaque retour reçu des stations est inscrit dans le
contrat RegistreKorko, sur le réseau de test Fuji. Les transactions partent
en tâche de fond, dans l'ordre d'arrivée : une location n'attend jamais la
blockchain. Au démarrage, le cloud lit le parc dans le contrat ; si Fuji ne
répond pas, il démarre avec le parc de secours de korko.STATIONS.

La clé privée du cloud est lue dans .env (jamais versionné), l'adresse du
contrat dans registre_korko.json (écrit par deployer.py).
"""

import json
import os
import queue
import sys
import threading
import time

from eth_account import Account
from eth_utils import abi_to_signature, function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import (ContractLogicError, TimeExhausted,
                             TransactionNotFound, Web3RPCError)
from web3.middleware import SignAndSendRawMiddlewareBuilder

RPC_FUJI = "https://api.avax-test.network/ext/bc/C/rpc"
EXPLORATEUR = "https://testnet.snowtrace.io"
DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_CLE = os.path.join(DOSSIER, ".env")
FICHIER_REGISTRE = os.path.join(DOSSIER, "registre_korko.json")
PREFIXE_BALISE = "korko-"
DELAI_RPC = 10         # secondes avant d'abandonner une requête à Fuji
DELAI_RECU = 120       # secondes d'attente maximale d'une confirmation
PAUSE_RESEAU = 5       # secondes avant de réessayer si Fuji est injoignable

#: Fuji injoignable, réponse d'erreur du nœud, ou page HTML d'un portail wifi
ERREURS_RESEAU = (OSError, Web3RPCError, json.JSONDecodeError)


class TransactionAnnulee(Exception):
    """La transaction a été minée, mais le contrat l'a annulée."""


def lire_cle_privee():
    """Lit KORKO_CLE_PRIVEE dans le fichier .env."""
    with open(FICHIER_CLE, encoding="utf-8-sig") as fichier:
        for ligne in fichier:
            nom, _, valeur = ligne.partition("=")
            if nom.strip() == "KORKO_CLE_PRIVEE":
                return valeur.strip().strip("\"'")
    raise KeyError("KORKO_CLE_PRIVEE absente de %s" % FICHIER_CLE)


def compte_du_cloud():
    """Retourne le compte du cloud, seul autorisé à écrire au registre."""
    return Account.from_key(lire_cle_privee())


def connecter_web3(fournisseur, compte):
    """Retourne un client Web3 qui signe ses transactions avec ce compte."""
    w3 = Web3(fournisseur)
    signeur = SignAndSendRawMiddlewareBuilder.build(compte)
    w3.middleware_onion.inject(signeur, layer=0)
    w3.eth.default_account = compte.address
    return w3


def connecter_fuji():
    """Retourne un client Web3 branché sur Fuji avec le compte du cloud."""
    fournisseur = Web3.HTTPProvider(RPC_FUJI,
                                    request_kwargs={"timeout": DELAI_RPC})
    return connecter_web3(fournisseur, compte_du_cloud())


def ouvrir_registre(w3):
    """Retourne le contrat déployé, décrit par registre_korko.json."""
    with open(FICHIER_REGISTRE, encoding="utf-8") as fichier:
        deploiement = json.load(fichier)
    return w3.eth.contract(address=deploiement["adresse"],
                           abi=deploiement["abi"], decode_tuples=True)


def connecter():
    """Retourne le registre sur Fuji, prêt à signer avec la clé du cloud."""
    return ouvrir_registre(connecter_fuji())


def attendre(w3, hash_transaction):
    """Attend la confirmation ; lève TransactionAnnulee si elle échoue."""
    recu = w3.eth.wait_for_transaction_receipt(hash_transaction,
                                               timeout=DELAI_RECU)
    if recu.status != 1:
        raise TransactionAnnulee(hash_transaction.to_0x_hex())
    return recu


def nommer_refus(registre, erreur):
    """Retourne le nom de l'erreur du contrat, ex. « StationInconnue »."""
    donnees = str(getattr(erreur, "data", ""))
    for element in registre.abi:
        if element["type"] != "error":
            continue
        signature = abi_to_signature(element)
        selecteur = function_signature_to_4byte_selector(signature).hex()
        if donnees.startswith("0x" + selecteur):
            return element["name"]
    return str(erreur)


def lien(hash_transaction):
    """Retourne l'adresse de la transaction sur l'explorateur Snowtrace."""
    return "%s/tx/%s" % (EXPLORATEUR, hash_transaction.to_0x_hex())


def lien_adresse(adresse):
    """Retourne l'adresse du contrat sur l'explorateur Snowtrace."""
    return "%s/address/%s" % (EXPLORATEUR, adresse)


def numero(balise):
    """« korko-07 » devient 7."""
    return int(balise.removeprefix(PREFIXE_BALISE))


def balise(numero_planche):
    """7 devient « korko-07 »."""
    return "%s%02d" % (PREFIXE_BALISE, numero_planche)


def lire_parc_du_contrat(registre):
    """Retourne le parc inscrit dans le contrat : {balise: station}."""
    parc = {}
    for numero_planche in registre.functions.listerPlanches().call():
        planche = registre.functions.lirePlanche(numero_planche).call()
        parc[balise(numero_planche)] = planche.stationOrigine
    return parc


def lire_parc(registre, secours):
    """Retourne le parc du contrat, ou le parc de secours si Fuji se tait.

    `secours` a la forme de korko.STATIONS : {station: balises}.
    """
    try:
        return lire_parc_du_contrat(registre)
    except ERREURS_RESEAU as erreur:
        print("CHAÎNE injoignable, parc de secours : %s" % erreur,
              file=sys.stderr)
        return {b: code for code, balises in secours.items() for b in balises}


class Publieur(threading.Thread):
    """Inscrit les événements des stations dans le registre, un par un.

    Chaque événement est signé une seule fois. Si la réponse du nœud se
    perd, l'essai suivant renvoie la même transaction signée : l'événement
    ne peut pas être inscrit deux fois.
    """

    def __init__(self, registre, compte, journaliser):
        super().__init__(daemon=True)
        self.registre = registre
        self.compte = compte
        self.journaliser = journaliser
        self.file = queue.Queue()
        self.nonce, self.signee = None, None
        self.dernier_incident = None

    def publier(self, evenement):
        """Met en file un DEPART, un RETOUR ou une ETRANGERE."""
        self.file.put(evenement)

    def run(self):
        """Inscrit la file dans l'ordre ; réessaie tant que Fuji se tait."""
        while True:
            evenement = self.file.get()
            try:
                while not self.inscrire(evenement):
                    time.sleep(PAUSE_RESEAU)
            except Exception as erreur:
                # une erreur imprévue (événement mal formé…) ne doit pas
                # arrêter la file
                self.signee = None
                self.journaliser("CHAÎNE ignoré : %r (%r)"
                                 % (evenement, erreur))

    def inscrire(self, evenement):
        """Vrai si l'événement est traité, faux s'il faut réessayer."""
        resume = "%s %s en %s" % (evenement["evenement"],
                                  evenement["balise"], evenement["station"])
        try:
            if self.signee is None:
                self.nonce, self.signee = self.signer(evenement)
            envoi = self.signee.raw_transaction
            self.registre.w3.eth.send_raw_transaction(envoi)
        except ContractLogicError as erreur:
            self.journaliser("CHAÎNE refus : %s (%s)"
                             % (resume, nommer_refus(self.registre, erreur)))
            return True
        except ERREURS_RESEAU as erreur:
            if not self.deja_envoyee():
                self.signaler_incident("CHAÎNE en échec : %s (%s)"
                                       % (resume, erreur))
                return False
        hash_transaction, self.signee = self.signee.hash, None
        self.dernier_incident = None
        self.confirmer(resume, hash_transaction)
        return True

    def signer(self, evenement):
        """Retourne le nonce et la transaction signée de l'événement."""
        adresse = self.compte.address
        nonce = self.registre.w3.eth.get_transaction_count(adresse, "pending")
        transaction = self.appel_pour(evenement).build_transaction(
            {"from": adresse, "nonce": nonce})
        return nonce, self.compte.sign_transaction(transaction)

    def deja_envoyee(self):
        """Vrai si le nœud connaît déjà la transaction signée.

        Si une autre transaction du même compte a pris son nonce, elle ne
        sera jamais minée : on l'oublie pour la signer à nouveau.
        """
        if self.signee is None:
            return False
        eth = self.registre.w3.eth
        try:
            eth.get_transaction(self.signee.hash)
            return True
        except TransactionNotFound:
            if self.nonce < eth.get_transaction_count(self.compte.address):
                self.signee = None
        except ERREURS_RESEAU:
            pass
        return False

    def confirmer(self, resume, hash_transaction):
        """Attend la confirmation, sans jamais réémettre la transaction."""
        try:
            attendre(self.registre.w3, hash_transaction)
        except TransactionAnnulee:
            self.journaliser("CHAÎNE annulée : %s : %s"
                             % (resume, lien(hash_transaction)))
        except (TimeExhausted, *ERREURS_RESEAU):
            self.journaliser("CHAÎNE non confirmée : %s : %s"
                             % (resume, lien(hash_transaction)))
        else:
            self.journaliser("CHAÎNE %s : %s"
                             % (resume, lien(hash_transaction)))

    def signaler_incident(self, message):
        """Journalise un incident une seule fois, pas à chaque essai."""
        if message != self.dernier_incident:
            self.journaliser(message)
        self.dernier_incident = message

    def appel_pour(self, evenement):
        """Un DEPART s'inscrit en départ ; un RETOUR ou une ETRANGERE
        (planche rendue à une autre station) en retour."""
        fonctions = self.registre.functions
        enregistrer = (fonctions.enregistrerDepart
                       if evenement["evenement"] == "DEPART"
                       else fonctions.enregistrerRetour)
        return enregistrer(numero(evenement["balise"]), evenement["station"],
                           round(evenement["t"] * 1000))
