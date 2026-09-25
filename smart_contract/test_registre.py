"""
test_registre.py — vérifie RegistreKorko et le Publieur sur une blockchain
locale en mémoire (eth-tester) : ni tokens, ni réseau une fois solc 0.8.24
téléchargé (au premier lancement).

    .venv\\Scripts\\python -m unittest smart_contract.test_registre -v
"""

import unittest

from eth_account import Account
from eth_tester.exceptions import TransactionFailed
from eth_utils import ValidationError
from web3 import EthereumTesterProvider, Web3
from web3.exceptions import ContractCustomError

from smart_contract import chaine, deployer

STATIONS_TEST = {"A": {"korko-01", "korko-02"}, "B": {"korko-03"}}
DEPART_01 = {"t": 74.5, "station": "A", "balise": "korko-01",
             "evenement": "DEPART"}


class NoeudLocal(EthereumTesterProvider):
    """eth-tester qui répond à un refus comme un vrai nœud Avalanche.

    Un nœud renvoie une erreur JSON-RPC, que web3 traduit en
    ContractCustomError ou en Web3RPCError. EthereumTesterProvider lève à
    la place ses propres exceptions, que chaine.py n'a pas à connaître.
    """

    def make_request(self, methode, parametres):
        espace, _, nom = methode.partition("_")
        executer = self.api_endpoints[espace][nom]
        try:
            resultat = executer(self.ethereum_tester, parametres)
        except TransactionFailed as refus:
            return self.erreur(3, "execution reverted", refus.args[0])
        except ValidationError as refus:
            return self.erreur(-32000, str(refus), b"")
        return {"jsonrpc": "2.0", "id": 0, "result": resultat}

    def erreur(self, code, message, octets):
        if isinstance(octets, Exception):
            octets = octets.args[0]
        donnees = "0x" + octets.hex()
        erreur = {"code": code, "message": message, "data": donnees}
        return {"jsonrpc": "2.0", "id": 0, "error": erreur}


class TestRegistre(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.abi, cls.bytecode = deployer.compiler()

    def setUp(self):
        fournisseur = NoeudLocal()
        banque = Web3(fournisseur)
        self.compte = Account.create()
        banque.eth.send_transaction({"from": banque.eth.accounts[0],
                                     "to": self.compte.address,
                                     "value": Web3.to_wei(1, "ether")})
        self.autre = banque.eth.accounts[1]
        self.w3 = chaine.connecter_web3(fournisseur, self.compte)
        self.registre = deployer.deployer(self.w3, self.abi, self.bytecode)
        deployer.inscrire_parc(self.registre, STATIONS_TEST)
        self.fonctions = self.registre.functions

    def envoyer(self, appel):
        return chaine.attendre(self.w3, appel.transact())

    def planche(self, numero):
        return self.fonctions.lirePlanche(numero).call()

    def publieur(self, journal):
        return chaine.Publieur(self.registre, self.compte, journal.append)

    def assertRefus(self, erreur, appel, expediteur=None):
        """Le contrat refuse l'appel avec l'erreur nommée."""
        selecteur = Web3.keccak(text=erreur)[:4].to_0x_hex()
        options = {"from": expediteur} if expediteur else {}
        with self.assertRaises(ContractCustomError) as refus:
            appel.transact(options)
        self.assertTrue(refus.exception.data.startswith(selecteur))

    def test_le_parc_initial_est_inscrit(self):
        self.assertEqual(self.fonctions.proprietaire().call(),
                         self.compte.address)
        self.assertEqual(chaine.lire_parc(self.registre, {}),
                         {"korko-01": "A", "korko-02": "A", "korko-03": "B"})
        self.assertEqual(self.fonctions.listerStations().call(), ["A", "B"])
        station_a = self.fonctions.lireStation("A").call()
        self.assertEqual(station_a.nombrePlanches, 2)

    def test_chaque_planche_et_chaque_station_sont_uniques(self):
        self.assertRefus("PlancheExistante(uint16)",
                         self.fonctions.ajouterPlanche(1, "B"))
        self.assertRefus("StationExistante(string)",
                         self.fonctions.ajouterStation("A"))
        self.assertRefus("CodeVide()", self.fonctions.ajouterStation(""))

    def test_une_planche_ou_une_station_inconnue_est_refusee(self):
        self.assertRefus("StationInconnue(string)",
                         self.fonctions.ajouterPlanche(9, "Z"))
        self.assertRefus("PlancheInconnue(uint16)",
                         self.fonctions.retirerPlanche(99))
        self.assertRefus("PlancheInconnue(uint16)",
                         self.fonctions.enregistrerRetour(99, "A", 1))

    def test_une_station_se_retire_une_fois_vide(self):
        self.assertRefus("StationNonVide(string,uint32)",
                         self.fonctions.retirerStation("B"))
        self.envoyer(self.fonctions.retirerPlanche(3))
        self.envoyer(self.fonctions.retirerStation("B"))
        self.assertEqual(self.fonctions.listerStations().call(), ["A"])

    def test_une_station_occupee_ne_se_ferme_pas(self):
        self.envoyer(self.fonctions.retirerPlanche(3))
        self.envoyer(self.fonctions.enregistrerRetour(1, "B", 1000))
        self.assertRefus("StationOccupee(string,uint16)",
                         self.fonctions.retirerStation("B"))

    def test_depart_puis_retour_dans_une_autre_station(self):
        recu = self.envoyer(self.fonctions.enregistrerDepart(1, "A", 74500))
        depart = self.registre.events.Depart().process_receipt(recu)[0]
        self.assertEqual((depart.args.numero, depart.args.instantMs),
                         (1, 74500))
        self.assertTrue(self.planche(1).enMer)
        recu = self.envoyer(self.fonctions.enregistrerRetour(1, "B", 90000))
        retour = self.registre.events.Retour().process_receipt(recu)[0]
        self.assertEqual(retour.args.station, "B")
        planche = self.planche(1)
        self.assertEqual((planche.enMer, planche.stationOrigine,
                          planche.derniereStation, planche.nombreDeparts),
                         (False, "A", "B", 1))

    def test_une_planche_en_mer_peut_etre_retiree(self):
        self.envoyer(self.fonctions.enregistrerDepart(1, "A", 74500))
        self.envoyer(self.fonctions.retirerPlanche(1))
        self.assertFalse(self.planche(1).existe)
        self.assertEqual(sorted(self.fonctions.listerPlanches().call()),
                         [2, 3])
        self.assertEqual(self.fonctions.lireStation("A").call()
                         .nombrePlanches, 1)

    def test_seul_le_proprietaire_ecrit(self):
        fonctions = self.fonctions
        for appel in (fonctions.ajouterStation("X"),
                      fonctions.retirerStation("B"),
                      fonctions.ajouterPlanche(9, "A"),
                      fonctions.retirerPlanche(1),
                      fonctions.enregistrerDepart(1, "A", 1),
                      fonctions.enregistrerRetour(1, "A", 1)):
            with self.subTest(appel.fn_name):
                self.assertRefus("NonAutorise(address)", appel, self.autre)

    def test_le_publieur_inscrit_un_depart(self):
        journal = []
        self.assertTrue(self.publieur(journal).inscrire(DEPART_01))
        self.assertTrue(self.planche(1).enMer)
        self.assertIn("CHAÎNE DEPART korko-01 en A : https://", journal[0])

    def test_le_publieur_inscrit_une_etrangere_comme_retour(self):
        publieur = self.publieur([])
        publieur.inscrire(DEPART_01)
        publieur.inscrire(dict(DEPART_01, evenement="ETRANGERE",
                               station="B"))
        planche = self.planche(1)
        self.assertEqual((planche.enMer, planche.derniereStation),
                         (False, "B"))

    def test_le_publieur_journalise_un_refus(self):
        journal = []
        publieur = self.publieur(journal)
        self.assertTrue(publieur.inscrire(dict(DEPART_01, station="Z")))
        self.assertEqual(journal,
                         ["CHAÎNE refus : DEPART korko-01 en Z "
                          "(StationInconnue)"])

    def test_un_envoi_deja_parvenu_n_est_pas_double(self):
        publieur = self.publieur([])
        publieur.nonce, publieur.signee = publieur.signer(DEPART_01)
        self.w3.eth.send_raw_transaction(publieur.signee.raw_transaction)
        self.assertTrue(publieur.inscrire(DEPART_01))
        self.assertEqual(self.planche(1).nombreDeparts, 1)

    def test_un_nonce_pris_ailleurs_est_signe_a_nouveau(self):
        publieur = self.publieur([])
        publieur.nonce, publieur.signee = publieur.signer(DEPART_01)
        self.envoyer(self.fonctions.ajouterStation("C"))
        self.assertFalse(publieur.inscrire(DEPART_01))
        self.assertTrue(publieur.inscrire(DEPART_01))
        self.assertEqual(self.planche(1).nombreDeparts, 1)

    def test_fuji_injoignable(self):
        hors_ligne = Web3(Web3.HTTPProvider(
            "http://127.0.0.1:9", exception_retry_configuration=None))
        registre = hors_ligne.eth.contract(address=self.registre.address,
                                           abi=self.abi)
        self.assertEqual(chaine.lire_parc(registre, STATIONS_TEST)["korko-03"],
                         "B")
        journal = []
        publieur = chaine.Publieur(registre, self.compte, journal.append)
        self.assertFalse(publieur.inscrire(DEPART_01))
        self.assertFalse(publieur.inscrire(DEPART_01))
        self.assertEqual(len(journal), 1)


if __name__ == "__main__":
    unittest.main()
