import unittest

import photo_verification as photo


def observation(**changes):
    data = {"planche_visible": True, "photo_exploitable": True, "numero_lisible": True,
            "numero_lu": "01", "etat": "sans_defaut_visible", "defaut": "",
            "certitude": "elevee"}
    data.update(changes)
    return data


class PhotoVerificationTests(unittest.TestCase):
    def test_confirms_only_matching_legible_and_clear_board(self):
        result = photo.classer(observation(), "korko-01")
        self.assertEqual(result["statut"], "conforme")
        self.assertEqual(result["titre"], "La planche est conforme")

    def test_other_board_and_visible_damage_are_informational_results(self):
        self.assertEqual(photo.classer(observation(numero_lu="02"), "korko-01")["statut"],
                         "autre_planche")
        damaged = photo.classer(observation(etat="defaut_visible", defaut="fissure"), "korko-01")
        self.assertEqual(damaged["statut"], "abimee")
        self.assertIn("fissure", damaged["message"])

    def test_uncertain_or_unreadable_evidence_never_confirms(self):
        for changes in ({"certitude": "faible"}, {"numero_lisible": False},
                        {"planche_visible": False}, {"etat": "incertain"}):
            self.assertEqual(photo.classer(observation(**changes), "korko-01")["statut"], "reprendre")

    def test_retry_after_seconds_is_preserved(self):
        self.assertEqual(photo._retry_after({"Retry-After": "37"}), 37.0)


if __name__ == "__main__":
    unittest.main()
