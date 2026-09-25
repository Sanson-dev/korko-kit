"""
test_paiement.py — vérifie les horaires, les fiches clients, le prestataire
simulé et la caisse, sans réseau.

    .venv\\Scripts\\python -m unittest paiement.test_paiement -v
"""

import os
import tempfile
import time
import unittest

from paiement import caisse, clients, horaires, prestataire

CLIENT = {"prenom": " Anastasia ", "nom": "Surf",
          "telephone": "06 12 34 56 78"}
CARTE = {"type": "carte", "marque": "Visa", "derniers4": "4242",
         "expiration": "12/30"}


class TestHoraires(unittest.TestCase):

    def test_le_simulateur_commence_a_9_h(self):
        self.assertEqual(horaires.texte_heure(0), "09:00")
        self.assertEqual(horaires.texte_heure(13 * 3600 + 59 * 60), "22:59")

    def test_les_locations_ferment_a_22_h(self):
        self.assertTrue(horaires.locations_ouvertes(13 * 3600 - 1))
        self.assertFalse(horaires.locations_ouvertes(13 * 3600))

    def test_la_limite_est_le_23_h_suivant(self):
        self.assertEqual(horaires.limite_retour(0), 14 * 3600)
        apres_23_h = 14 * 3600 + 1800
        self.assertEqual(horaires.limite_retour(apres_23_h),
                         apres_23_h + 23 * 3600 + 1800)

    def test_une_vraie_station_donne_l_heure_locale(self):
        date = time.mktime((2026, 9, 25, 22, 30, 0, 0, 0, -1))
        self.assertEqual(horaires.texte_heure(date), "22:30")
        self.assertFalse(horaires.locations_ouvertes(date))


class TestFiches(unittest.TestCase):

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "clients.json")
        self.fichier = clients.Fichier(self.chemin)

    def tearDown(self):
        self.dossier.cleanup()

    def test_le_telephone_est_normalise(self):
        self.assertEqual(clients.normaliser_telephone("06 12 34 56 78"),
                         "+33612345678")
        self.assertEqual(clients.normaliser_telephone("+44 20 7946 0958"),
                         "+442079460958")
        with self.assertRaises(clients.ErreurFiche):
            clients.normaliser_telephone("12")

    def test_la_carte_n_est_gardee_que_par_ses_4_derniers_chiffres(self):
        fiche = self.fichier.enregistrer(CLIENT, CARTE)
        self.assertEqual(fiche["prenom"], "Anastasia")
        self.assertEqual(fiche["moyen"]["libelle"], "Visa •••• 4242")
        with open(self.chemin, encoding="utf-8") as fichier:
            self.assertNotIn("4242 4242", fichier.read())

    def test_la_deuxieme_location_reprend_le_moyen_enregistre(self):
        self.fichier.enregistrer(CLIENT, CARTE)
        relu = clients.Fichier(self.chemin)
        fiche = relu.enregistrer(CLIENT, {"type": "enregistre"})
        self.assertEqual(fiche["moyen"]["libelle"], "Visa •••• 4242")

    def test_sans_moyen_enregistre_il_faut_en_choisir_un(self):
        with self.assertRaises(clients.ErreurFiche):
            self.fichier.enregistrer(CLIENT, {"type": "enregistre"})
        with self.assertRaises(clients.ErreurFiche):
            self.fichier.enregistrer(dict(CLIENT, nom=" "), CARTE)

    def test_le_portefeuille_crypto_est_cree_une_seule_fois(self):
        premiere = self.fichier.enregistrer(CLIENT, {"type": "crypto"})
        adresse = premiere["portefeuille"]["adresse"]
        self.fichier.enregistrer(CLIENT, CARTE)
        seconde = self.fichier.enregistrer(CLIENT, {"type": "crypto"})
        self.assertEqual(seconde["portefeuille"]["adresse"], adresse)
        self.assertIn(adresse[:6], seconde["moyen"]["libelle"])


class TestPrestataire(unittest.TestCase):

    def setUp(self):
        self.journal = []
        self.prestataire = prestataire.Prestataire(None, self.journal.append)
        self.fiche = {"moyen": clients.carte(CARTE)}

    def test_la_caution_est_bloquee_sans_rien_debiter(self):
        autorisation = self.prestataire.bloquer(self.fiche)
        self.assertEqual((autorisation.etat, autorisation.montant,
                          autorisation.debite), ("bloquée", 300.0, None))

    def test_une_carte_de_test_refusee(self):
        fiche = {"moyen": clients.carte(dict(CARTE, derniers4="0002"))}
        with self.assertRaises(prestataire.RefusDePaiement):
            self.prestataire.bloquer(fiche)

    def test_la_fin_debite_le_prix_et_libere_la_caution(self):
        autorisation = self.prestataire.bloquer(self.fiche)
        self.prestataire.debiter(autorisation, 1.2345)
        self.assertEqual((autorisation.etat, autorisation.debite),
                         ("libérée", 1.23))

    def test_la_caution_entiere_peut_etre_debitee(self):
        autorisation = self.prestataire.bloquer(self.fiche)
        self.prestataire.debiter(autorisation, 300.0)
        self.assertEqual((autorisation.etat, autorisation.debite),
                         ("débitée", 300.0))

    def test_sans_sequestre_la_crypto_est_refusee(self):
        with self.assertRaises(prestataire.RefusDePaiement):
            self.prestataire.bloquer({"moyen": {"type": "crypto"}})


class TestCaisse(unittest.TestCase):

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        fichier = clients.Fichier(os.path.join(self.dossier.name, "c.json"))
        self.journal = []
        paiement = prestataire.Prestataire(None, self.journal.append)
        self.caisse = caisse.Caisse(fichier, paiement, self.journal.append)
        self.location = {"balise": "korko-01"}
        self.caisse.reserver(self.location, CLIENT, CARTE, 0)

    def tearDown(self):
        self.dossier.cleanup()

    def test_la_reservation_souhaite_une_bonne_session(self):
        resume = self.caisse.resume(self.location)
        self.assertEqual(resume["messages"],
                         [{"heure": "09:00", "texte": caisse.BIENVENUE}])
        self.assertEqual(resume["caution"]["etat"], "bloquée")
        self.assertEqual(resume["client"]["telephone"], "+33612345678")

    def test_le_retour_debite_le_prix_et_libere_la_caution(self):
        self.caisse.terminer(self.location, 1.24, 600)
        resume = self.caisse.resume(self.location)
        self.assertEqual((resume["debite"], resume["caution"]["etat"]),
                         (1.24, "libérée"))
        self.assertEqual(resume["messages"][-1]["texte"],
                         "Merci Anastasia ! 🏄 1,24 € débités sur Visa •••• "
                         "4242. Votre caution de 300,00 € est libérée. À la "
                         "prochaine fois ! 🌊")

    def test_une_annulation_ne_debite_rien(self):
        self.caisse.annuler(self.location, 60)
        resume = self.caisse.resume(self.location)
        self.assertEqual((resume["debite"], resume["caution"]["etat"]),
                         (0.0, "libérée"))
        self.assertIn("Réservation annulée, Anastasia",
                      resume["messages"][-1]["texte"])

    def test_sans_retour_a_23_h_la_caution_est_debitee(self):
        self.caisse.saisir_caution(self.location, 14 * 3600)
        resume = self.caisse.resume(self.location)
        self.assertEqual((resume["debite"], resume["caution"]["etat"]),
                         (300.0, "débitée"))
        self.assertIn("23:00", resume["messages"][-1]["heure"])
        self.assertIn("1 location(s) · 300,00 € payés",
                      self.caisse.fiches()[0])

    def test_un_refus_ne_reserve_rien(self):
        location = {"balise": "korko-02"}
        with self.assertRaises(caisse.REFUS):
            self.caisse.reserver(location, dict(CLIENT, telephone="?"),
                                 CARTE, 0)
        self.assertNotIn("autorisation", location)


if __name__ == "__main__":
    unittest.main()
