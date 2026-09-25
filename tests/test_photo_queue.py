import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import mon_cloud as cloud
import photo_queue
import photo_verification as photo


def observation(board="01", state="sans_defaut_visible", defect=""):
    return {"planche_visible": True, "photo_exploitable": True, "numero_lisible": True,
            "numero_lu": board, "etat": state, "defaut": defect, "certitude": "elevee"}


class PhotoQueueFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), cloud.Cloud)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_queue = cloud.photo_tasks
        self.old_generation = cloud.demo_generation
        self.queue = photo_queue.FilePhotos(os.path.join(self.temp.name, "korko.sqlite3"))
        cloud.photo_tasks = self.queue
        cloud.demo_generation = max(1, self.old_generation + 1)
        self.queue.set_demo_generation(cloud.demo_generation)
        cloud.client_sessions.clear()
        cloud.client_sessions["client-test"] = {
            "client": "+33600000000", "station": "A", "balise": "korko-01",
            "etat": "retournée", "session_id": "session-test-123", "retour_a": 204586.0,
            "duree": 60.0, "montant": 0.20, "retour_station": "A",
            "reservation_status": "Terminée",
        }
        with open(os.path.join(os.path.dirname(__file__), "..", "test_images",
                               "planche-1-intacte.png"), "rb") as stream:
            self.image = stream.read()
        self.task_id = "task-test-12345678"

    def tearDown(self):
        self.queue.stop()
        cloud.client_sessions.clear()
        cloud.photo_tasks = self.old_queue
        cloud.demo_generation = self.old_generation
        self.temp.cleanup()

    def post_photo(self, task_id=None, image=None):
        payload = {"identifiant": "client-test", "session_id": "session-test-123",
                   "task_id": task_id or self.task_id, "generation": cloud.demo_generation,
                   "balise": "korko-99",  # Le serveur doit ignorer ce champ.
                   "image": "data:image/png;base64," + base64.b64encode(image or self.image).decode("ascii")}
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/photo-verification" % self.http.server_port,
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def get_client(self):
        url = "http://127.0.0.1:%d/api/client?identifiant=client-test" % self.http.server_port
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read())

    def test_api_outage_keeps_photo_durable_for_retry(self):
        status, data = self.post_photo()
        self.assertEqual(status, 202)
        self.assertEqual(data["tache"]["status"], "pending")
        with self.queue._database() as db:
            row = db.execute("SELECT * FROM photo_tasks").fetchone()
            image_name = row["image_name"]
        path = self.queue.photo_path(image_name)
        self.assertTrue(os.path.isfile(path))
        with patch.object(photo, "analyser_image", side_effect=photo.PhotoErreur(
                "Service indisponible", 504, retryable=True, retry_after=60)):
            self.assertTrue(self.queue.process_one())
        task = self.queue.get(self.task_id)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["attempts"], 1)
        self.assertTrue(os.path.isfile(path))

    def test_restart_recovers_processing_and_client_can_read_result(self):
        self.post_photo()
        claimed = self.queue.claim_next()
        self.assertEqual(claimed["status"], "processing")
        second = photo_queue.FilePhotos(self.queue.db_path)
        second.recover_interrupted()
        cloud.photo_tasks = second
        with patch.object(photo, "analyser_image", return_value=observation()):
            self.assertTrue(second.process_one())
        self.assertEqual(second.get(self.task_id)["status"], "done")
        client = self.get_client()
        self.assertEqual(client["session_id"], "session-test-123")
        self.assertEqual(client["photo_task"]["status"], "done")
        self.assertEqual(client["photo_task"]["result"]["statut"], "conforme")

    def test_resend_does_not_duplicate_and_trusts_server_board(self):
        _, first = self.post_photo()
        _, second = self.post_photo()
        self.assertEqual(first["tache"]["task_id"], second["tache"]["task_id"])
        with self.queue._database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM photo_tasks").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT board FROM photo_tasks").fetchone()[0], "korko-01")

    def test_billing_error_stops_attempts_and_retains_status(self):
        self.post_photo()
        with patch.object(photo, "analyser_image", side_effect=photo.PhotoErreur(
                "Crédits API épuisés", 503)):
            self.assertTrue(self.queue.process_one())
            self.assertFalse(self.queue.process_one())
        task = self.queue.get(self.task_id)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["attempts"], 1)
        self.assertIn("Crédits API", task["error"])
        self.assertEqual(os.listdir(self.queue.photo_dir), [])

    def test_completed_photo_is_deleted_and_does_not_change_rental(self):
        self.post_photo()
        before = dict(cloud.client_sessions["client-test"])
        with patch.object(photo, "analyser_image", return_value=observation()):
            self.assertTrue(self.queue.process_one())
        self.assertEqual(self.queue.get(self.task_id)["result"]["titre"], "La planche est conforme")
        self.assertEqual(cloud.client_sessions["client-test"], before)
        self.assertEqual(cloud.planches["korko-01"]["statut"], "au râtelier")


    def test_both_sample_photos_pass_through_upload_queue_and_result_poll(self):
        samples = ("planche-1-intacte.png", "planche-1-cassee.png")
        task_ids = ("sample-intacte-123456", "sample-cassee-123456")
        for name, task_id in zip(samples, task_ids):
            path = os.path.join(os.path.dirname(__file__), "..", "test_images", name)
            with open(path, "rb") as stream:
                _, accepted = self.post_photo(task_id, stream.read())
            self.assertEqual(accepted["tache"]["status"], "pending")
        with patch.object(photo, "analyser_image", side_effect=[
                observation(), observation(state="defaut_visible", defect="fissure visible")]):
            self.assertTrue(self.queue.process_one())
            self.assertTrue(self.queue.process_one())
        self.assertEqual(self.queue.get(task_ids[0])["result"]["statut"], "conforme")
        self.assertEqual(self.queue.get(task_ids[1])["result"]["statut"], "abimee")
        self.assertEqual(cloud.client_json("client-test")["photo_task"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
