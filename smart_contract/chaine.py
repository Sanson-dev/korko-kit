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

from smart_contract.boite_envoi import BoiteEnvoi

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

#: le compte du cloud signe depuis plusieurs fils (registre, séquestre) :
#: une transaction à la fois, jusqu'à sa confirmation, sinon deux fils
#: prendraient le même nonce
VERROU_CLOUD = threading.Lock()


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


def creer_publieur(registre, journaliser):
    """Retourne le publieur du cloud, avec sa boîte d'envoi persistante."""
    return Publieur(registre, compte_du_cloud(), BoiteEnvoi(), journaliser)


def resumer(evenement):
    """« DEPART korko-01 en A » : l'événement en quelques mots."""
    return "%s %s en %s" % (evenement.get("evenement"),
                            evenement.get("balise"), evenement.get("station"))


def empreinte(envoi):
    """Retourne le hash de la transaction signée de l'envoi."""
    return Web3.keccak(hexstr=envoi["brut"])


#: ligne du journal selon l'issue d'un envoi
MESSAGES = {"inscrit": "CHAÎNE %s : %s",
            "refusé": "CHAÎNE refus : %s (%s)",
            "annulée": "CHAÎNE annulée : %s : %s",
            "non confirmée": "CHAÎNE non confirmée : %s : %s",
            "ignoré": "CHAÎNE ignoré : %s (%s)"}


class Publieur(threading.Thread):
    """Inscrit les événements des stations dans le registre, un par un.

    Chaque événement passe d'abord par la boîte d'envoi persistante
    (boite_envoi.py) : une coupure ou un redémarrage du cloud ne le perd
    pas, et un événement reçu deux fois n'est inscrit qu'une fois. Il est
    signé une seule fois : si la réponse du nœud se perd, même après un
    redémarrage, l'essai suivant renvoie la même transaction signée.
    """

    def __init__(self, registre, compte, boite, journaliser):
        super().__init__(daemon=True)
        self.registre = registre
        self.compte = compte
        self.boite = boite
        self.journaliser = journaliser
        self.file = queue.Queue()
        self.dernier_incident = None
        for envoi in boite.en_attente():   # repris après un redémarrage
            self.file.put(envoi)

    def publier(self, evenement):
        """Dépose un DEPART, un RETOUR ou une ETRANGERE dans la boîte
        d'envoi, puis le met en file ; un événement déjà reçu est ignoré."""
        envoi = self.boite.deposer(evenement)
        if envoi:
            self.file.put(envoi)

    def run(self):
        """Inscrit la file dans l'ordre ; réessaie tant que Fuji se tait."""
        while True:
            envoi = self.file.get()
            try:
                while not self.inscrire_seul(envoi):
                    time.sleep(PAUSE_RESEAU)
            except Exception as erreur:
                # une erreur imprévue (événement mal formé…) ne doit pas
                # arrêter la file
                self.clore(envoi, "ignoré", repr(erreur))

    def inscrire_seul(self, envoi):
        """Inscrit l'événement pendant qu'aucun autre fil du cloud ne signe ;
        vrai s'il est traité, faux s'il faut réessayer."""
        with VERROU_CLOUD:
            return self.inscrire(envoi)

    def inscrire(self, envoi):
        """Vrai si l'événement est traité, faux s'il faut réessayer."""
        try:
            if envoi["brut"] is None:
                self.signer(envoi)
            self.registre.w3.eth.send_raw_transaction(envoi["brut"])
        except ContractLogicError as erreur:
            self.clore(envoi, "refusé", nommer_refus(self.registre, erreur))
            return True
        except ERREURS_RESEAU as erreur:
            if not self.deja_envoyee(envoi):
                self.signaler_incident("CHAÎNE en échec : %s (%s)"
                                       % (resumer(envoi["evenement"]), erreur))
                return False
        self.dernier_incident = None
        self.confirmer(envoi)
        return True

    def signer(self, envoi):
        """Signe la transaction de l'envoi et garde la signature."""
        adresse = self.compte.address
        nonce = self.registre.w3.eth.get_transaction_count(adresse, "pending")
        transaction = self.appel_pour(envoi["evenement"]).build_transaction(
            {"from": adresse, "nonce": nonce})
        signee = self.compte.sign_transaction(transaction)
        envoi["nonce"] = nonce
        envoi["brut"] = signee.raw_transaction.to_0x_hex()
        self.boite.signer(envoi["event_id"], nonce, envoi["brut"])

    def deja_envoyee(self, envoi):
        """Vrai si le nœud connaît déjà la transaction signée.

        Si une autre transaction du même compte a pris son nonce, elle ne
        sera jamais minée : on l'oublie pour la signer à nouveau.
        """
        if envoi["brut"] is None:
            return False
        eth = self.registre.w3.eth
        try:
            eth.get_transaction(empreinte(envoi))
            return True
        except TransactionNotFound:
            if envoi["nonce"] < eth.get_transaction_count(self.compte.address):
                envoi["brut"] = None
                self.boite.signer(envoi["event_id"], None, None)
        except ERREURS_RESEAU:
            pass
        return False

    def confirmer(self, envoi):
        """Attend la confirmation, sans jamais réémettre la transaction."""
        preuve = lien(empreinte(envoi))
        try:
            attendre(self.registre.w3, empreinte(envoi))
        except TransactionAnnulee:
            self.clore(envoi, "annulée", preuve)
        except (TimeExhausted, *ERREURS_RESEAU):
            self.clore(envoi, "non confirmée", preuve)
        else:
            self.clore(envoi, "inscrit", preuve)

    def clore(self, envoi, statut, preuve):
        """Sort l'envoi de l'attente et le journalise."""
        self.boite.clore(envoi["event_id"], statut, preuve)
        self.journaliser(MESSAGES[statut]
                         % (resumer(envoi["evenement"]), preuve))

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
