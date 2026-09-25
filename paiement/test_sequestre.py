"""
test_sequestre.py — vérifie SequestreKorko et le fil Sequestre sur une
blockchain locale en mémoire (eth-tester) : ni réseau, ni AVAX.

    .venv\\Scripts\\python -m unittest paiement.test_sequestre -v
"""

import os
import secrets
import tempfile
import unittest
from unittest import mock

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractCustomError

from paiement import crypto, deployer_sequestre, prestataire
from smart_contract import chaine, deployer
from smart_contract.test_registre import NoeudLocal

CAUTION = crypto.en_wei(300.0)
PRIX = crypto.en_wei(1.24)


def nouvelle_reference():
    """Un identifiant de location aléatoire, comme ceux du prestataire."""
    return "0x" + secrets.token_hex(32)


def nouvelle_autorisation():
    """Une caution crypto de 300 €, en attente de blocage."""
    return prestataire.Autorisation(nouvelle_reference(), "crypto",
                                    "Crypto (Fuji)")


def debuts(journal):
    """Les lignes du journal, sans l'identifiant ni le lien."""
    return [ligne.split(" 0x")[0] for ligne in journal]


class NoeudCapricieux(NoeudLocal):
    """Nœud local dont la connexion lâche à la demande.

    Les `coupures` prochaines requêtes n'arrivent pas ; si `distrait`,
    chaque transaction est exécutée mais sa première réponse se perd.
    """

    def __init__(self):
        super().__init__()
        self.coupures = 0
        self.distrait = False
        self.executees = set()

    def make_request(self, methode, parametres):
        if self.coupures:
            self.coupures -= 1
            raise ConnectionError("Fuji injoignable")
        reponse = super().make_request(methode, parametres)
        if self.distrait and methode == "eth_sendRawTransaction":
            if parametres[0] not in self.executees:
                self.executees.add(parametres[0])
                raise ConnectionError("réponse perdue")
        return reponse


class TestSequestre(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.abi, cls.bytecode = deployer_sequestre.compiler()

    def setUp(self):
        self.noeud = NoeudCapricieux()
        self.tresor, self.client = Web3(self.noeud).eth.accounts[:2]
        self.korko = Account.create()
        self.virer(self.korko.address, Web3.to_wei(1, "ether"))
        self.w3 = chaine.connecter_web3(self.noeud, self.korko)
        self.contrat = deployer.deployer(self.w3, self.abi, self.bytecode)
        self.fonctions = self.contrat.functions
        self.journal = []
        self.sequestre = crypto.Sequestre(self.contrat, self.korko,
                                          self.journal.append)

    def virer(self, adresse, valeur):
        Web3(self.noeud).eth.send_transaction(
            {"from": self.tresor, "to": adresse, "value": valeur})

    def solde(self, adresse):
        return self.w3.eth.get_balance(adresse)

    def envoyer(self, appel, options=None):
        return chaine.attendre(self.w3, appel.transact(options or {}))

    def bloquer(self, location):
        options = {"from": self.client, "value": CAUTION}
        return self.envoyer(self.fonctions.bloquer(location), options)

    def evenement(self, nom, recu):
        return self.contrat.events[nom]().process_receipt(recu)[0].args

    def cout(self, recu):
        return recu.gasUsed * recu.effectiveGasPrice

    def portefeuille(self, valeur=0):
        """Un portefeuille de démo, garni de `valeur` wei."""
        portefeuille = crypto.creer_portefeuille()
        if valeur:
            self.virer(portefeuille["adresse"], valeur)
        return portefeuille

    def transaction_du_lien(self, texte):
        return self.w3.eth.get_transaction(texte.rsplit("/", 1)[1])

    def envoi_de_sept_wei(self, destinataire):
        virement = {"to": destinataire, "value": 7, "gas": 21_000,
                    "maxFeePerGas": 2 * self.w3.eth.gas_price,
                    "maxPriorityFeePerGas": self.w3.eth.max_priority_fee,
                    "chainId": self.w3.eth.chain_id}
        return crypto.Envoi(self.w3, self.korko, virement)

    def traiter_la_file(self):
        """Démarre le fil et attend qu'il ait vidé sa file."""
        self.sequestre.start()
        self.sequestre.file.join()

    def vider_korko(self, reste):
        """Ne laisse que `reste` wei au compte du cloud."""
        prix = 2 * self.w3.eth.gas_price
        valeur = self.solde(self.korko.address) - reste - 21_000 * prix
        self.w3.eth.send_transaction({"to": self.tresor, "value": valeur,
                                      "gas": 21_000, "gasPrice": prix})

    def demarrer(self, deploye):
        """crypto.demarrer, sur le nœud local au lieu de Fuji et du .env.

        Le fichier de déploiement n'est écrit que si `deploye`.
        """
        dossier = self.enterContext(tempfile.TemporaryDirectory())
        fichier = os.path.join(dossier, "sequestre_korko.json")
        self.enterContext(mock.patch.object(crypto, "FICHIER_SEQUESTRE",
                                            fichier))
        self.enterContext(mock.patch.object(chaine, "connecter_fuji",
                                            lambda: self.w3))
        self.enterContext(mock.patch.object(chaine, "compte_du_cloud",
                                            lambda: self.korko))
        if deploye:
            deployer_sequestre.sauvegarder(self.contrat)
        return crypto.demarrer(self.journal.append)

    def assertRefus(self, erreur, appel, options=None):
        """Le contrat refuse l'appel avec l'erreur nommée."""
        selecteur = Web3.keccak(text=erreur)[:4].to_0x_hex()
        with self.assertRaises(ContractCustomError) as refus:
            appel.transact(options or {})
        self.assertTrue(refus.exception.data.startswith(selecteur))

    def test_korko_est_le_deployeur(self):
        self.assertEqual(self.fonctions.korko().call(), self.korko.address)

    def test_bloquer_garde_la_caution(self):
        location = nouvelle_reference()
        bloquee = self.evenement("CautionBloquee", self.bloquer(location))
        self.assertEqual((bloquee.client, bloquee.montant),
                         (self.client, CAUTION))
        caution = self.fonctions.lireCaution(location).call()
        self.assertEqual((caution.client, caution.reglee, caution.montant),
                         (self.client, False, CAUTION))
        self.assertEqual(self.solde(self.contrat.address), CAUTION)

    def test_bloquer_refuse_un_montant_nul_ou_un_identifiant_pris(self):
        location = nouvelle_reference()
        appel = self.fonctions.bloquer(location)
        self.assertRefus("MontantNul()", appel, {"from": self.client})
        self.bloquer(location)
        self.assertRefus("LocationExistante(bytes32)", appel,
                         {"from": self.client, "value": CAUTION})

    def test_cloturer_paie_korko_et_rend_le_reste_exact(self):
        for du in (PRIX, 0, CAUTION):
            with self.subTest(du=du):
                location = nouvelle_reference()
                self.bloquer(location)
                client = self.solde(self.client)
                korko = self.solde(self.korko.address)
                recu = self.envoyer(self.fonctions.cloturer(location, du))
                payee = self.evenement("LocationPayee", recu)
                self.assertEqual((payee.paye, payee.rendu), (du, CAUTION - du))
                self.assertEqual(self.solde(self.client),
                                 client + CAUTION - du)
                self.assertEqual(self.solde(self.korko.address),
                                 korko + du - self.cout(recu))
                self.assertEqual(self.solde(self.contrat.address), 0)

    def test_saisir_garde_toute_la_caution(self):
        location = nouvelle_reference()
        self.bloquer(location)
        client, korko = self.solde(self.client), self.solde(self.korko.address)
        recu = self.envoyer(self.fonctions.saisir(location))
        saisie = self.evenement("CautionSaisie", recu)
        self.assertEqual(saisie.montant, CAUTION)
        self.assertEqual(self.solde(self.client), client)
        self.assertEqual(self.solde(self.korko.address),
                         korko + CAUTION - self.cout(recu))
        self.assertEqual(self.solde(self.contrat.address), 0)

    def test_une_caution_se_regle_une_seule_fois(self):
        location = nouvelle_reference()
        self.bloquer(location)
        self.envoyer(self.fonctions.cloturer(location, PRIX))
        self.assertTrue(self.fonctions.lireCaution(location).call().reglee)
        self.assertRefus("CautionReglee(bytes32)",
                         self.fonctions.cloturer(location, PRIX))
        self.assertRefus("CautionReglee(bytes32)",
                         self.fonctions.saisir(location))

    def test_un_reglement_impossible_est_refuse(self):
        location = nouvelle_reference()
        self.assertRefus("CautionInconnue(bytes32)",
                         self.fonctions.saisir(location))
        self.bloquer(location)
        self.assertRefus("MontantExcessif(uint256,uint256)",
                         self.fonctions.cloturer(location, CAUTION + 1))

    def test_seul_korko_regle(self):
        location = nouvelle_reference()
        self.bloquer(location)
        for appel in (self.fonctions.cloturer(location, PRIX),
                      self.fonctions.saisir(location)):
            with self.subTest(appel.fn_name):
                self.assertRefus("NonAutorise(address)", appel,
                                 {"from": self.client})

    def test_le_fil_approvisionne_puis_bloque(self):
        portefeuille = self.portefeuille()
        autorisation = nouvelle_autorisation()
        self.sequestre.executer_blocage(autorisation, portefeuille["cle"])
        self.assertEqual(autorisation.etat, "bloquée")
        self.assertEqual(len(autorisation.liens), 1)
        self.assertTrue(autorisation.liens[0].startswith(
            chaine.EXPLORATEUR + "/tx/0x"))
        caution = self.fonctions.lireCaution(autorisation.reference).call()
        self.assertEqual((caution.client, caution.montant),
                         (portefeuille["adresse"], CAUTION))
        self.assertEqual(debuts(self.journal), ["CRYPTO approvisionnement",
                                                "CRYPTO caution bloquée"])

    def test_le_portefeuille_est_complete_de_ce_qui_manque(self):
        deja = crypto.en_wei(100.0)
        portefeuille = self.portefeuille(deja)
        autorisation = nouvelle_autorisation()
        self.sequestre.executer_blocage(autorisation, portefeuille["cle"])
        virement = self.transaction_du_lien(self.journal[0])
        blocage = self.transaction_du_lien(autorisation.liens[0])
        besoin = CAUTION + crypto.GAZ_BLOQUER * blocage.maxFeePerGas
        self.assertEqual(virement.value, besoin - deja)

    def test_un_portefeuille_garni_n_est_pas_approvisionne(self):
        portefeuille = self.portefeuille(Web3.to_wei(1, "ether"))
        autorisation = nouvelle_autorisation()
        self.sequestre.executer_blocage(autorisation, portefeuille["cle"])
        self.assertEqual(autorisation.etat, "bloquée")
        self.assertEqual(debuts(self.journal), ["CRYPTO caution bloquée"])

    def test_le_fil_rend_le_reste_au_client(self):
        portefeuille = self.portefeuille()
        autorisation = nouvelle_autorisation()
        self.sequestre.executer_blocage(autorisation, portefeuille["cle"])
        avant = self.solde(portefeuille["adresse"])
        self.sequestre.executer_reglement(autorisation, 1.24)
        self.assertEqual(self.solde(portefeuille["adresse"]),
                         avant + CAUTION - PRIX)
        self.assertEqual((autorisation.etat, autorisation.debite),
                         ("libérée", 1.24))
        self.assertEqual(len(autorisation.liens), 2)
        self.assertEqual(debuts(self.journal)[-1], "CRYPTO caution libérée")

    def test_le_fil_saisit_la_caution_non_rendue(self):
        portefeuille = self.portefeuille()
        autorisation = nouvelle_autorisation()
        self.sequestre.executer_blocage(autorisation, portefeuille["cle"])
        avant = self.solde(portefeuille["adresse"])
        self.sequestre.executer_reglement(autorisation, 300.0)
        self.assertEqual(self.solde(portefeuille["adresse"]), avant)
        self.assertEqual(self.solde(self.contrat.address), 0)
        self.assertEqual((autorisation.etat, autorisation.debite),
                         ("débitée", 300.0))
        self.assertEqual(debuts(self.journal)[-1], "CRYPTO caution débitée")

    def test_la_file_bloque_avant_de_regler(self):
        autorisation = nouvelle_autorisation()
        self.sequestre.bloquer(autorisation, self.portefeuille()["cle"])
        self.sequestre.debiter(autorisation, 1.24)
        self.traiter_la_file()
        self.assertEqual(autorisation.etat, "libérée")
        self.assertEqual(debuts(self.journal), ["CRYPTO approvisionnement",
                                                "CRYPTO caution bloquée",
                                                "CRYPTO caution libérée"])

    def test_un_refus_passe_en_echec_sans_arreter_la_file(self):
        jamais_bloquee = nouvelle_autorisation()
        suivante = nouvelle_autorisation()
        self.sequestre.debiter(jamais_bloquee, 1.24)
        self.sequestre.bloquer(suivante, self.portefeuille()["cle"])
        self.traiter_la_file()
        self.assertEqual((jamais_bloquee.etat, jamais_bloquee.debite),
                         ("échec", None))
        self.assertEqual(self.journal[0], "CRYPTO échec du règlement %s : "
                         "CautionInconnue"
                         % crypto.abreger(jamais_bloquee.reference))
        self.assertEqual(suivante.etat, "bloquée")

    def test_un_cloud_a_sec_n_arrete_pas_la_file(self):
        garnie = nouvelle_autorisation()
        riche = self.portefeuille(Web3.to_wei(1, "ether"))
        self.sequestre.executer_blocage(garnie, riche["cle"])
        self.vider_korko(CAUTION - 1)
        nouvelle = nouvelle_autorisation()
        self.sequestre.bloquer(nouvelle, self.portefeuille()["cle"])
        self.sequestre.debiter(garnie, 300.0)
        self.traiter_la_file()
        self.assertEqual(nouvelle.etat, "échec")
        self.assertEqual(self.journal[1], "CRYPTO échec du blocage %s : "
                         "fonds insuffisants sur %s"
                         % (crypto.abreger(nouvelle.reference),
                            crypto.abreger(self.korko.address)))
        self.assertEqual(garnie.etat, "débitée")

    @mock.patch.object(crypto, "GAZ_BLOQUER", 30_000)  # trop peu de gaz
    def test_une_transaction_annulee_laisse_sa_preuve(self):
        autorisation = nouvelle_autorisation()
        self.sequestre.bloquer(autorisation, self.portefeuille()["cle"])
        self.traiter_la_file()
        self.assertEqual(autorisation.etat, "échec")
        [preuve] = autorisation.liens
        self.assertEqual(self.journal[-1], "CRYPTO échec du blocage %s : %s"
                         % (crypto.abreger(autorisation.reference), preuve))
        recu = self.w3.eth.get_transaction_receipt(preuve.rsplit("/", 1)[1])
        self.assertEqual(recu.status, 0)

    def test_sans_contrat_deploye_rien_ne_demarre(self):
        self.assertIsNone(self.demarrer(deploye=False))

    def test_demarrer_lance_le_fil_sur_le_contrat_deploye(self):
        sequestre = self.demarrer(deploye=True)
        self.assertTrue(sequestre.is_alive())
        autorisation = nouvelle_autorisation()
        sequestre.bloquer(autorisation, self.portefeuille()["cle"])
        sequestre.file.join()
        self.assertEqual(autorisation.etat, "bloquée")

    def test_une_reponse_perdue_n_entraine_aucun_double_envoi(self):
        portefeuille = self.portefeuille()
        autorisation = nouvelle_autorisation()
        self.noeud.distrait = True
        self.sequestre.executer_blocage(autorisation, portefeuille["cle"])
        self.assertEqual(autorisation.etat, "bloquée")
        compter = self.w3.eth.get_transaction_count
        self.assertEqual(compter(self.korko.address), 2)  # avec le déploiement
        self.assertEqual(compter(portefeuille["adresse"]), 1)
        self.assertEqual(len(self.noeud.executees), 2)  # réponses perdues

    @mock.patch.object(chaine, "PAUSE_RESEAU", 0)
    def test_fuji_injoignable_un_moment(self):
        portefeuille = self.portefeuille()
        autorisation = nouvelle_autorisation()
        self.noeud.coupures = 5
        self.sequestre.executer_blocage(autorisation, portefeuille["cle"])
        self.assertEqual(autorisation.etat, "bloquée")
        self.assertEqual(self.journal.count(
            "CRYPTO nouvel essai : Fuji injoignable"), 1)

    @mock.patch.object(chaine, "PAUSE_RESEAU", 0)
    def test_un_nonce_pris_ailleurs_est_signe_a_nouveau(self):
        destinataire = Account.create().address
        envoi = self.envoi_de_sept_wei(destinataire)
        envoi.signer()
        self.w3.eth.send_transaction({"to": destinataire, "value": 1})
        self.sequestre.insister(envoi.confirmer)
        self.assertEqual(self.solde(destinataire), 8)

    def test_un_envoi_deja_parvenu_n_est_pas_double(self):
        destinataire = Account.create().address
        envoi = self.envoi_de_sept_wei(destinataire)
        envoi.signer()
        self.w3.eth.send_raw_transaction(envoi.signee.raw_transaction)
        self.assertEqual(envoi.confirmer(), envoi.signee.hash)
        self.assertEqual(self.solde(destinataire), 7)


if __name__ == "__main__":
    unittest.main()
