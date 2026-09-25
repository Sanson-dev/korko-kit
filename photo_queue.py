"""File durable d'analyses photo, stockée en SQLite et sur disque."""

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import photo_verification

MAX_RETRY_SECONDS = 300
POLL_SECONDS = 1.0
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def utc_now():
    """Horloge murale de planification, indépendante du champ événementiel KORKO t."""
    return datetime.now(timezone.utc)


def utc_text(value=None):
    return (value or utc_now()).isoformat(timespec="milliseconds")


class FileTacheErreur(Exception):
    def __init__(self, message, code=409):
        super().__init__(message)
        self.code = code


class FilePhotos:
    """Conserve les photos et leur suivi SQLite jusqu’au résultat ou à l’échec."""

    def __init__(self, db_path=None):
        configured = db_path or os.environ.get("KORKO_DB_PATH")
        if not configured:
            configured = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "korko-data",
                "korko.sqlite3",
            )
        self.db_path = os.path.abspath(configured)
        self.photo_dir = self.db_path + ".pending"
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.photo_dir, exist_ok=True)
        self.lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._create_schema()

    # --- Connexions et schéma SQLite ---

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _database(self):
        """Valide ou annule la transaction et ferme toujours sa connexion."""
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _create_schema(self):
        with self._database() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS photo_tasks (
                task_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                board TEXT NOT NULL,
                station TEXT NOT NULL,
                generation INTEGER NOT NULL,
                retour_t REAL NOT NULL,
                duree REAL NOT NULL,
                montant REAL NOT NULL,
                mime TEXT NOT NULL,
                image_name TEXT,
                image_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, image_sha256)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS photo_tasks_due
                         ON photo_tasks(status, next_attempt_at, created_at)""")
            db.execute("""CREATE INDEX IF NOT EXISTS photo_tasks_client
                         ON photo_tasks(client_id, created_at)""")
            db.execute("""CREATE TABLE IF NOT EXISTS cloud_settings (
                         key TEXT PRIMARY KEY, value TEXT NOT NULL)""")

    def set_demo_generation(self, generation):
        """Mémorise la génération utilisée pour isoler les démonstrations."""
        with self._database() as db:
            db.execute(
                "INSERT INTO cloud_settings(key,value) VALUES('demo_generation',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(int(generation)),),
            )

    def get_demo_generation(self, default=0):
        with self._database() as db:
            row = db.execute(
                "SELECT value FROM cloud_settings WHERE key='demo_generation'"
            ).fetchone()
        try:
            return int(row[0]) if row else int(default)
        except (ValueError, TypeError):
            return int(default)

    # --- Fichiers image et représentation publique des tâches ---

    def _image_name(self, session_id, digest, mime):
        stem = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:20]
        return stem + "-" + digest + EXTENSIONS[mime]

    def _write_photo(self, image_name, image):
        """Écrit puis remplace atomiquement le fichier avant validation SQLite."""
        destination = os.path.join(self.photo_dir, image_name)
        handle, temporary = tempfile.mkstemp(
            prefix=".korko-", suffix=".tmp", dir=self.photo_dir
        )
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(image)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination

    def _unlink(self, image_name):
        if not image_name:
            return
        path = os.path.join(self.photo_dir, image_name)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _task(row):
        if row is None:
            return None
        task = dict(row)
        task["result"] = (
            json.loads(task.pop("result_json")) if task.get("result_json") else None
        )
        # La réponse HTTP ne révèle jamais les chemins internes ni le hash de l'image.
        task.pop("image_name", None)
        task.pop("image_sha256", None)
        task.pop("mime", None)
        task.pop("next_attempt_at", None)
        task.pop("created_at", None)
        task.pop("updated_at", None)
        task.pop("client_id", None)
        task.pop("generation", None)
        task.pop("retour_t", None)
        task.pop("duree", None)
        task.pop("montant", None)
        task.pop("station", None)
        task.pop("board", None)
        return task

    # --- Dépôt durable et consultation depuis l’API ---

    def enqueue(
        self,
        *,
        task_id,
        client_id,
        session_id,
        board,
        station,
        generation,
        retour_t,
        duree,
        montant,
        mime,
        image
    ):
        """Enregistre une photo, déduplique les renvois et réarme les échecs."""
        if not isinstance(task_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{8,80}", task_id
        ):
            raise FileTacheErreur("Identifiant de tâche invalide.", 400)
        digest = hashlib.sha256(image).hexdigest()
        instant = utc_text()
        with self.lock:
            db = self._connect()
            image_name = None
            photo_written = False
            try:
                # Réserver l’écriture avant de rechercher un doublon : deux
                # requêtes simultanées ne doivent pas créer deux tâches.
                db.execute("BEGIN IMMEDIATE")
                by_id = db.execute(
                    "SELECT * FROM photo_tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                if by_id:
                    if (
                        by_id["client_id"] != client_id
                        or by_id["session_id"] != session_id
                        or by_id["image_sha256"] != digest
                    ):
                        raise FileTacheErreur(
                            "Cet identifiant de tâche est déjà utilisé pour une autre photo."
                        )
                    if by_id["status"] == "failed":
                        image_name = self._image_name(session_id, digest, mime)
                        self._write_photo(image_name, image)
                        photo_written = True
                        db.execute(
                            "UPDATE photo_tasks SET status='pending', attempts=0, error=NULL, "
                            "result_json=NULL, image_name=?, mime=?, next_attempt_at=?, updated_at=? "
                            "WHERE task_id=?",
                            (image_name, mime, instant, instant, task_id),
                        )
                        by_id = db.execute(
                            "SELECT * FROM photo_tasks WHERE task_id=?", (task_id,)
                        ).fetchone()
                    db.commit()
                    self._wake.set()
                    return self._task(by_id), False

                duplicate = db.execute(
                    "SELECT * FROM photo_tasks WHERE session_id=? AND image_sha256=?",
                    (session_id, digest),
                ).fetchone()
                if duplicate:
                    if duplicate["status"] == "failed":
                        image_name = self._image_name(session_id, digest, mime)
                        self._write_photo(image_name, image)
                        photo_written = True
                        db.execute(
                            "UPDATE photo_tasks SET status='pending', attempts=0, error=NULL, "
                            "result_json=NULL, image_name=?, mime=?, next_attempt_at=?, updated_at=? "
                            "WHERE task_id=?",
                            (image_name, mime, instant, instant, duplicate["task_id"]),
                        )
                        duplicate = db.execute(
                            "SELECT * FROM photo_tasks WHERE task_id=?",
                            (duplicate["task_id"],),
                        ).fetchone()
                    db.commit()
                    self._wake.set()
                    return self._task(duplicate), False

                image_name = self._image_name(session_id, digest, mime)
                self._write_photo(image_name, image)
                photo_written = True
                db.execute(
                    """INSERT INTO photo_tasks(
                    task_id,client_id,session_id,board,station,generation,retour_t,duree,montant,
                    mime,image_name,image_sha256,status,attempts,next_attempt_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',0,?,?,?)""",
                    (
                        task_id,
                        client_id,
                        session_id,
                        board,
                        station,
                        int(generation),
                        float(retour_t),
                        float(duree),
                        float(montant),
                        mime,
                        image_name,
                        digest,
                        instant,
                        instant,
                        instant,
                    ),
                )
                row = db.execute(
                    "SELECT * FROM photo_tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                if photo_written and image_name:
                    referenced = db.execute(
                        "SELECT 1 FROM photo_tasks WHERE image_name=?", (image_name,)
                    ).fetchone()
                    if not referenced:
                        self._unlink(image_name)
                raise
            finally:
                db.close()
        self._wake.set()
        return self._task(row), True

    def get(self, task_id, client_id=None, session_id=None):
        query = "SELECT * FROM photo_tasks WHERE task_id=?"
        args = [task_id]
        if client_id is not None:
            query += " AND client_id=?"
            args.append(client_id)
        if session_id is not None:
            query += " AND session_id=?"
            args.append(session_id)
        with self._database() as db:
            row = db.execute(query, args).fetchone()
        return self._task(row)

    def latest_for_client(self, client_id, session_id=None, generation=None):
        query = "SELECT * FROM photo_tasks WHERE client_id=?"
        args = [client_id]
        if session_id is not None:
            query += " AND session_id=?"
            args.append(session_id)
        if generation is not None:
            query += " AND generation=?"
            args.append(int(generation))
        query += " ORDER BY rowid DESC LIMIT 1"
        with self._database() as db:
            row = db.execute(query, args).fetchone()
        return self._task(row)

    def session_snapshot(self, client_id, generation=None):
        """Reconstruit le reçu minimal pour reprendre le suivi après redémarrage."""
        query = "SELECT * FROM photo_tasks WHERE client_id=?"
        args = [client_id]
        if generation is not None:
            query += " AND generation=?"
            args.append(int(generation))
        query += " ORDER BY rowid DESC LIMIT 1"
        with self._database() as db:
            row = db.execute(query, args).fetchone()
        if not row:
            return None
        return {
            "client": "",
            "station": row["station"],
            "balise": row["board"],
            "etat": "retournée",
            "session_id": row["session_id"],
            "retour_a": row["retour_t"],
            "duree": row["duree"],
            "montant": row["montant"],
            "retour_station": row["station"],
            "reservation_status": "Terminée",
        }

    # --- Reprise, traitement et tentatives différées ---

    def recover_interrupted(self):
        """Remet en attente les analyses interrompues par l’arrêt du processus."""
        instant = utc_text()
        with self._database() as db:
            db.execute(
                "UPDATE photo_tasks SET status='pending', next_attempt_at=?, updated_at=? "
                "WHERE status='processing'",
                (instant, instant),
            )

    def claim_next(self):
        """Réserve atomiquement la première tâche dont le délai est écoulé."""
        instant = utc_text()
        with self.lock:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM photo_tasks WHERE status='pending' "
                    "AND next_attempt_at<=? ORDER BY rowid LIMIT 1",
                    (instant,),
                ).fetchone()
                if row is None:
                    db.commit()
                    return None
                db.execute(
                    "UPDATE photo_tasks SET status='processing', attempts=attempts+1, updated_at=? "
                    "WHERE task_id=? AND status='pending'",
                    (instant, row["task_id"]),
                )
                claimed = db.execute(
                    "SELECT * FROM photo_tasks WHERE task_id=?", (row["task_id"],)
                ).fetchone()
                db.commit()
                return dict(claimed)
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def finish(self, task_id, result):
        """Persiste le résultat avant de supprimer les octets de la photo."""
        instant = utc_text()
        with self._database() as db:
            row = db.execute(
                "SELECT image_name FROM photo_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            db.execute(
                "UPDATE photo_tasks SET status='done', result_json=?, error=NULL, image_name=NULL, "
                "next_attempt_at=?, updated_at=? WHERE task_id=?",
                (json.dumps(result, ensure_ascii=False), instant, instant, task_id),
            )
        if row:
            self._unlink(row["image_name"])

    def retry_later(self, task_id, message, delay):
        """Planifie une reprise en temps réel et conserve la photo sur disque."""
        instant = utc_now()
        next_at = utc_text(instant + timedelta(seconds=max(0.0, float(delay))))
        with self._database() as db:
            db.execute(
                "UPDATE photo_tasks SET status='pending', error=?, next_attempt_at=?, updated_at=? "
                "WHERE task_id=?",
                (message, next_at, utc_text(instant), task_id),
            )

    def fail(self, task_id, message):
        instant = utc_text()
        with self._database() as db:
            row = db.execute(
                "SELECT image_name FROM photo_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            db.execute(
                "UPDATE photo_tasks SET status='failed', error=?, image_name=NULL, "
                "next_attempt_at=?, updated_at=? WHERE task_id=?",
                (message, instant, instant, task_id),
            )
        if row:
            self._unlink(row["image_name"])

    def photo_path(self, image_name):
        return os.path.join(self.photo_dir, image_name)

    def process_one(self, analyser=None, on_log=None):
        """Analyse une tâche sans modifier location, paiement ou état de planche."""
        task = self.claim_next()
        if task is None:
            return False
        try:
            image_path = self.photo_path(task["image_name"])
            with open(image_path, "rb") as stream:
                image = stream.read(photo_verification.MAX_IMAGE_BYTES + 1)
            if len(image) > photo_verification.MAX_IMAGE_BYTES:
                raise photo_verification.PhotoErreur("La photo en file dépasse 8 Mo.")
            analyse = analyser or photo_verification.analyser_image
            observation = analyse(task["mime"], image)
            result = photo_verification.classer(observation, task["board"])
            self.finish(task["task_id"], result)
            if on_log:
                on_log("PHOTO %s : %s" % (task["board"], result["statut"]))
        except photo_verification.PhotoErreur as erreur:
            if erreur.retryable:
                # Retry-After prime sur le recul exponentiel ; cette attente
                # suit l’horloge réelle, jamais le temps accéléré des stations.
                exponent = max(0, int(task["attempts"]) - 1)
                delay = (
                    erreur.retry_after
                    if erreur.retry_after is not None
                    else min(5 * (2**exponent), MAX_RETRY_SECONDS)
                )
                self.retry_later(
                    task["task_id"], str(erreur), min(float(delay), 86400.0)
                )
                if on_log:
                    on_log("PHOTO %s : nouvel essai planifié" % task["board"])
            else:
                self.fail(task["task_id"], str(erreur))
                if on_log:
                    on_log(
                        "PHOTO %s : analyse arrêtée (%s)"
                        % (task["board"], type(erreur).__name__)
                    )
        except Exception as erreur:
            self.fail(
                task["task_id"],
                "Erreur interne de l'analyse photo. Vérifiez les journaux du cloud.",
            )
            if on_log:
                on_log(
                    "PHOTO %s : erreur interne %s"
                    % (task["board"], type(erreur).__name__)
                )
        return True

    def start(self, on_log=None):
        """Récupère les tâches interrompues avant de lancer le travailleur."""
        if self._thread and self._thread.is_alive():
            return
        self.recover_interrupted()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(on_log,), name="korko-photo-worker", daemon=True
        )
        self._thread.start()

    def _run(self, on_log=None):
        while not self._stop.is_set():
            try:
                if self.process_one(on_log=on_log):
                    continue
            except Exception as erreur:
                if on_log:
                    on_log("PHOTO file : erreur interne %s" % type(erreur).__name__)
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def stop(self, timeout=3):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
