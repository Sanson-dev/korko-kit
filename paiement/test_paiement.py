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
          "telephone": "06 12 34 56 78", "appareil": "telephone-1"}
CARTE = {"type": "carte", "marque": "Visa", "derniers4": "4242",
         "expiration": "12/30"}


def enregistrer(fichier, client, moyen):
    """Prépare puis enregistre la fiche, comme la caisse après un blocage."""
    fiche = fichier.preparer(client, moyen)
    fichier.retenir(fiche)
    return fiche


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
        for saisie in ("+33 6 12 34 56 78", "+33 (0)6 12 34 56 78",
                       "0033 6 12 34 56 78"):
            self.assertEqual(clients.normaliser_telephone(saisie),
                             "+33612345678")
        self.assertEqual(clients.normaliser_telephone("+44 20 7946 0958"),
                         "+442079460958")
        with self.assertRaises(clients.ErreurFiche):
            clients.normaliser_telephone("12")

    def test_la_carte_n_est_gardee_que_par_ses_4_derniers_chiffres(self):
        recue = dict(CARTE, numero="4242 4242 4242 4242", cvc="123")
        fiche = enregistrer(self.fichier, CLIENT, recue)
        self.assertEqual(fiche["prenom"], "Anastasia")
        self.assertEqual(sorted(fiche["moyen"]),
                         ["derniers4", "expiration", "libelle", "type"])
        with open(self.chemin, encoding="utf-8") as fichier:
            self.assertNotIn("4242 4242", fichier.read())

    def test_un_autre_telephone_ne_paie_pas_avec_la_carte(self):
        enregistrer(self.fichier, CLIENT, CARTE)
        intrus = dict(CLIENT, appareil="telephone-2")
        with self.assertRaises(clients.ErreurFiche):
            enregistrer(self.fichier, intrus, {"type": "enregistre"})

    def test_une_carte_remplacee_ne_resert_pas_ailleurs(self):
        enregistrer(self.fichier, CLIENT, CARTE)
        intrus = dict(CLIENT, appareil="telephone-2")
        enregistrer(self.fichier, intrus, dict(CARTE, derniers4="1111"))
        with self.assertRaises(clients.ErreurFiche):
            enregistrer(self.fichier, CLIENT, {"type": "enregistre"})

    def test_la_deuxieme_location_reprend_le_moyen_enregistre(self):
        enregistrer(self.fichier, CLIENT, CARTE)
        relu = clients.Fichier(self.chemin)
        fiche = enregistrer(relu, CLIENT, {"type": "enregistre"})
        self.assertEqual(fiche["moyen"]["libelle"], "Visa •••• 4242")

    def test_sans_moyen_enregistre_il_faut_en_choisir_un(self):
        with self.assertRaises(clients.ErreurFiche):
            enregistrer(self.fichier, CLIENT, {"type": "enregistre"})

    def test_le_nom_est_obligatoire(self):
        with self.assertRaises(clients.ErreurFiche):
            enregistrer(self.fichier, dict(CLIENT, nom=" "), CARTE)

    def test_le_portefeuille_crypto_est_cree_une_seule_fois(self):
        premiere = enregistrer(self.fichier, CLIENT, {"type": "crypto"})
        adresse = premiere["portefeuille"]["adresse"]
        enregistrer(self.fichier, CLIENT, CARTE)
        seconde = enregistrer(self.fichier, CLIENT, {"type": "crypto"})
        self.assertEqual(seconde["portefeuille"]["adresse"], adresse)
        self.assertIn(adresse[:6], seconde["moyen"]["libelle"])

    def test_un_nouveau_portefeuille_crypto_peut_etre_demande(self):
        premiere = enregistrer(self.fichier, CLIENT, {"type": "crypto"})
        ancienne = premiere["portefeuille"]["adresse"]
        seconde = enregistrer(self.fichier, CLIENT,
                              {"type": "crypto", "nouveau": True})
        self.assertNotEqual(seconde["portefeuille"]["adresse"], ancienne)
        self.assertEqual(seconde["anciens_portefeuilles"][0]["adresse"],
                         ancienne)


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

    def test_la_reservation_laisse_5_minutes_pour_partir(self):
        resume = self.caisse.resume(self.location)
        self.assertEqual(resume["messages"],
                         [{"heure": "09:00",
                           "texte": caisse.RESERVATION % "01"}])
        self.assertEqual(resume["caution"]["etat"], "bloquée")
        self.assertEqual(resume["client"]["telephone"], "+33612345678")

    def test_le_depart_souhaite_une_bonne_session_avec_l_heure(self):
        self.caisse.partir(self.location, 3900)
        texte = self.caisse.resume(self.location)["messages"][-1]["texte"]
        self.assertIn("Vous avez retiré la planche 01 à 10:05.", texte)
        self.assertIn("**vous devez nous rendre la planche avant 23 heures**",
                      texte)

    def test_le_retour_debite_le_prix_et_libere_la_caution(self):
        self.caisse.terminer(self.location, 1.24, 600)
        resume = self.caisse.resume(self.location)
        self.assertEqual((resume["debite"], resume["caution"]["etat"]),
                         (1.24, "libérée"))
        self.assertEqual(resume["messages"][-1]["texte"],
                         "Merci Anastasia ! 🏄 Planche 01 rendue à 09:10 : "
                         "1,24 € débités sur Visa •••• 4242. Votre caution "
                         "de 300,00 € est libérée. À la prochaine fois ! 🌊")

    def test_une_planche_pas_prise_a_temps_ne_coute_rien(self):
        self.caisse.expirer(self.location, 300)
        resume = self.caisse.resume(self.location)
        self.assertEqual((resume["debite"], resume["caution"]["etat"]),
                         (0.0, "libérée"))
        self.assertEqual(resume["messages"][-1]["texte"],
                         caisse.EXPIRATION % ("01", 10))

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

    def test_retour_tardif_ne_regle_pas_une_seconde_fois(self):
        self.caisse.saisir_caution(self.location, 14 * 3600)
        self.caisse.retour_tardif(self.location, 14 * 3600 + 120)
        resume = self.caisse.resume(self.location)
        self.assertEqual((resume["debite"], resume["caution"]["etat"]),
                         (300.0, "débitée"))
        self.assertIn("1 location(s) · 300,00 € payés", self.caisse.fiches()[0])
        self.assertIn("a bien été rendue", resume["messages"][-1]["texte"])

    def test_un_refus_ne_reserve_rien(self):
        location = {"balise": "korko-02"}
        with self.assertRaises(caisse.REFUS):
            self.caisse.reserver(location, dict(CLIENT, telephone="?"),
                                 CARTE, 0)
        self.assertNotIn("autorisation", location)

    def test_une_carte_refusee_n_est_pas_enregistree(self):
        autre = {"prenom": "Léa", "nom": "Vague", "telephone": "0699999999"}
        refusee = dict(CARTE, derniers4="0002")
        with self.assertRaises(caisse.REFUS):
            self.caisse.reserver({"balise": "korko-02"}, autre, refusee, 0)
        with self.assertRaises(caisse.REFUS):
            self.caisse.reserver({"balise": "korko-02"}, autre,
                                 {"type": "enregistre"}, 0)


if __name__ == "__main__":
    unittest.main()
