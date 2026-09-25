# KORKO — location autonome de planches de surf

KORKO est un prototype complet de station de location de planches de surf autonome. Le projet relie la détection physique des planches, une interface client mobile, la gestion des réservations et des paiements, un mode dégradé hors ligne, un suivi d'état/maintenance, une vérification photo et une traçabilité sur Avalanche Fuji.

Le dépôt contient également un simulateur de station permettant de jouer des départs, retours, perturbations radio, planches étrangères et journées complètes sans matériel physique.

> **Prototype / hackathon** — certaines briques sont volontairement simulées (carte bancaire, Apple Pay, SMS), tandis que la blockchain Avalanche Fuji et l'analyse photo OpenAI peuvent être réellement appelées.

---

## Architecture

```mermaid
flowchart LR
    U[Client / navigateur] -->|HTTP :9000| C[Cloud KORKO\nmon_cloud.py]
    U -. mode hors ligne .->|HTTP :9100| S[Station\nma_station.py]
    SIM[Simulateur\nkorko_sim.py\n:8080] -->|flux RSSI TCP :8420| S
    S -->|événements /evenements| C
    C --> DB[(SQLite\nkorko_cloud.sqlite3)]
    C --> PHOTO[(File photo durable\nkorko-data/)]
    PHOTO --> OAI[API OpenAI\nanalyse photo]
    C --> AVAX[Avalanche Fuji\nregistre + séquestre]
    C --> WEB[UI client + /admin]
```

### Ports par défaut

| Service | Port | Usage |
|---|---:|---|
| Cloud KORKO | `9000` | interface client, API, administration |
| Simulateur | `8080` | page de pilotage du simulateur |
| Flux simulateur | `8420` | flux TCP RSSI vers la station |
| API locale station | `9100` | location en mode hors ligne |

---

# Fonctionnalités

## Location et réservation

- réservation d'une planche disponible depuis l'interface web ;
- attribution de la planche la moins utilisée parmi celles disponibles ;
- réservation valable **10 minutes** ;
- annulation avant le départ ;
- blocage de la caution à la réservation ;
- démarrage automatique de la location lorsque la station détecte le départ physique ;
- clôture automatique au retour physique ;
- tarification de **0,20 € / minute** ;
- fermeture des nouvelles locations à **22 h** ;
- heure limite de retour à **23 h** ;
- saisie de la caution si la planche n'est pas rendue à l'heure limite ;
- prise en charge d'un **retour tardif** sans perdre la session active ;
- protection contre les événements de départ/retour dupliqués.

## Identification client

Lors de la première réservation, le client peut renseigner :

- prénom ;
- nom ;
- numéro de téléphone ;
- moyen de paiement.

La fiche client est ensuite conservée localement dans `paiement/clients.json`. Les numéros complets de carte et les CVC ne sont jamais enregistrés : seuls la marque, les quatre derniers chiffres et la date d'expiration sont conservés.

Le moyen enregistré ne peut être réutilisé que depuis un appareil déjà associé au client.

## Paiement et caution

Trois moyens sont disponibles :

- **carte bancaire** — simulation ;
- **Apple Pay** — simulation ;
- **crypto AVAX** — transactions réelles sur Avalanche Fuji.

Règles principales :

- caution : **300 €** ;
- prix : **0,20 €/min** ;
- la caution est bloquée lors de la réservation ;
- au retour normal, seul le prix de la location est débité et la caution restante est libérée ;
- à 23 h sans retour, la caution est saisie ;
- une réservation expirée ou annulée ne génère aucun débit.

Carte de démonstration acceptée :

```text
4242 4242 4242 4242
12/30
123
```

Pour simuler un refus, utiliser un numéro de carte terminant par `0002`.

## Blockchain Avalanche Fuji

Deux usages blockchain sont intégrés.

### Registre des planches

Chaque départ et retour reçu par le cloud est publié dans un registre public sur Avalanche Fuji.

- réseau : **Avalanche Fuji** — chain ID `43113` ;
- contrat registre : [`0xeb267Fd27cc6222178a3CbF2DadA98d6bd62D2F9`](https://testnet.snowtrace.io/address/0xeb267Fd27cc6222178a3CbF2DadA98d6bd62D2F9) ;
- compte cloud autorisé : `0xaB75b88e66DE5B217F8Ac9F89914d827b8Fb5bf8`.

Les écritures sont effectuées en tâche de fond : la location n'attend pas la confirmation blockchain. Une boîte d'envoi persistante permet de reprendre les transactions après une coupure réseau ou un redémarrage.

### Séquestre crypto

Le paiement crypto utilise le contrat `SequestreKorko` :

[`0x19118f120914576B30DcdBe32Ab23175C4CAB795`](https://testnet.snowtrace.io/address/0x19118f120914576B30DcdBe32Ab23175C4CAB795)

Un portefeuille de démonstration peut être créé pour le client. La caution est réellement placée dans le contrat sur Fuji puis réglée/libérée au retour.

## Détection physique des planches

`ma_station.py` analyse le RSSI reçu par les balises.

Configuration actuelle :

- seuil : `-80 dBm` ;
- silence de `10 s` avant de considérer une planche partie ;
- événements : `DEPART`, `RETOUR`, `ETRANGERE` ;
- horloge basée sur le champ `t` du flux, jamais sur l'heure du PC pour la logique métier.

Le cloud gère également plusieurs cas ambigus :

- départ d'une planche sans client armé ;
- départ ambigu avec plusieurs clients possibles ;
- client ayant réservé une planche mais en prenant physiquement une autre : la session peut être **réattribuée automatiquement** si le client est identifiable sans ambiguïté ;
- retour sans session active ;
- retour dupliqué.

## Planches étrangères et rééquilibrage

Les planches appartiennent à une station d'origine :

- station A : `korko-01`, `korko-02` ;
- station B : `korko-03`, `korko-04` ;
- station C : `korko-05`, `korko-06`.

Lorsqu'une planche d'une autre station est détectée :

- un événement `ETRANGERE` est enregistré ;
- une alerte de rééquilibrage apparaît dans l'administration ;
- la localisation de la planche est suivie ;
- l'administrateur peut confirmer le rééquilibrage ;
- une détection `ETRANGERE` **ne clôture pas arbitrairement une location active**.

## Rapport d'état et maintenance

Après un retour, le client peut déclarer :

- `OK` — tout va bien ;
- `MINOR` — petit problème / inspection nécessaire ;
- `DAMAGED` — planche abîmée.

Conséquences :

- `MINOR` marque la planche à inspecter ;
- `DAMAGED` place la planche en maintenance et empêche une nouvelle location ;
- l'administration permet de confirmer la réparation et la remise en service.

## Vérification photo

Le client peut ajouter une photo après le retour.

Le système :

- accepte JPEG, PNG et WebP ;
- limite l'image à **8 Mo** ;
- vérifie localement la signature du fichier ;
- stocke la demande dans une **file SQLite durable** ;
- reprend automatiquement l'analyse après un redémarrage ;
- déduplique les renvois ;
- appelle l'API OpenAI ;
- vérifie notamment que la planche est visible, que son numéro est lisible et si un défaut évident apparaît ;
- renvoie un résultat informatif sans modifier automatiquement la facturation ou l'état métier de la location.

Les anciennes tâches photo sont isolées par une `demo_generation` afin qu'un résultat provenant d'une ancienne démonstration ne réapparaisse pas après un reset.

## Mode hors ligne / dégradé

La station expose une API locale sur le port `9100`.

Un appareil ayant déjà obtenu une autorisation lors d'une connexion au cloud peut continuer à réserver une planche lorsque le cloud est indisponible.

Pendant la coupure :

- la station conserve son dernier état connu du parc ;
- elle n'attribue pas les planches connues comme étant en maintenance ;
- elle enregistre les réservations et événements physiques dans un journal local ;
- le navigateur suit localement l'état de la location.

Au retour du cloud :

- les événements sont rejoués dans l'ordre ;
- les timestamps originaux sont conservés ;
- le paiement est recalculé avec la durée réelle ;
- les événements déjà reçus sont ignorés grâce à des IDs stables ;
- la synchronisation blockchain reprend.

Le mode hors ligne utilise un jeton HMAC contenant seulement un identifiant d'autorisation, la station et une date d'expiration. Il ne contient aucune donnée bancaire ni numéro de téléphone.

## Persistance

Le projet reprend son état après redémarrage grâce à plusieurs stockages locaux :

| Fichier / dossier | Rôle |
|---|---|
| `korko_cloud.sqlite3` | état du cloud + boîte d'envoi blockchain |
| `korko-data/korko.sqlite3` | file d'analyse photo |
| `korko-data/korko.sqlite3.pending/` | photos en attente de traitement |
| `station_journal.ndjson` | événements station non synchronisés |
| `station_offline_state.json` | état local de la station |
| `paiement/clients.json` | fiches clients et moyens enregistrés |

## Administration

La page `/admin` permet notamment de consulter :

- état des stations ;
- état de chaque planche ;
- locations et écarts d'attribution ;
- planches à rééquilibrer ;
- rapports de condition ;
- maintenance ;
- fiches clients ;
- journal d'événements ;
- événements paiement / crypto / blockchain ;
- reset complet de la démonstration.

Le reset de démonstration :

- efface les locations et réservations temporaires ;
- remet le parc dans son état initial ;
- libère les cautions encore bloquées ;
- incrémente la génération utilisée par la file photo ;
- ne modifie pas le simulateur physique.

---

# Installation

## 1. Prérequis

- Python 3 avec `venv` et `pip` ;
- Git ;
- accès Internet pour Avalanche Fuji ;
- une clé privée Avalanche Fuji pour le cloud ;
- une clé API OpenAI si la vérification photo doit être utilisée.

Aucun build frontend n'est nécessaire : l'interface est en HTML/CSS/JavaScript statique.

## 2. Cloner le dépôt

```bash
git clone <URL_DU_DEPOT>
cd korko-kit
```

## 3. Créer l'environnement Python

### Linux / WSL / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r smart_contract/requirements.txt
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r smart_contract\requirements.txt
```

Les dépendances Web3, Ethereum tester, Solidity et les outils de test sont définis dans `smart_contract/requirements.txt`.

---

# Configuration des secrets

## 1. Clé privée du cloud Avalanche

Créer le fichier :

```text
smart_contract/.env
```

avec une seule ligne :

```text
KORKO_CLE_PRIVEE=0xVOTRE_CLE_PRIVEE
```

Pour bénéficier de toutes les fonctions blockchain et crypto, cette clé doit correspondre au compte cloud autorisé et disposer d'AVAX de test pour le gaz.

Une clé jetable permet de lancer une partie de l'application, mais les écritures dans les contrats déployés seront refusées.

> **Ne jamais publier cette clé.**

## 2. Clé API OpenAI

La clé est lue uniquement depuis la variable d'environnement `OPENAI_API_KEY`.

### Linux / WSL / macOS

```bash
export OPENAI_API_KEY="votre_cle_api"
```

### Windows PowerShell

```powershell
$env:OPENAI_API_KEY="votre_cle_api"
```

La variable doit être définie dans le terminal qui lance `mon_cloud.py`.

Sans cette variable, le reste du système fonctionne mais la vérification photo retourne une erreur de configuration.

## 3. Secret du mode hors ligne — recommandé

Par défaut, le prototype utilise un secret HMAC commun codé comme valeur de secours. Pour un environnement partagé, définir la **même valeur** côté cloud et station :

### Linux / WSL

```bash
export KORKO_OFFLINE_SECRET="un-secret-long-et-aleatoire"
```

### Windows PowerShell

```powershell
$env:KORKO_OFFLINE_SECRET="un-secret-long-et-aleatoire"
```

---

# Lancer la démonstration complète

Le plus simple est d'utiliser **trois terminaux**.

## Terminal 1 — Cloud

Activer l'environnement virtuel puis :

```bash
python mon_cloud.py
```

ou sans activation sous Linux/WSL :

```bash
.venv/bin/python mon_cloud.py
```

Le cloud écoute sur :

```text
http://localhost:9000
```

## Terminal 2 — Simulateur physique

```bash
python korko_sim.py
```

Interfaces créées :

```text
Contrôle : http://localhost:8080
Flux RSSI : tcp://localhost:8420
```

## Terminal 3 — Station

Pour connecter la station au simulateur précédent :

```bash
python ma_station.py --source localhost:8420
```

La station ouvre également :

```text
http://localhost:9100/offline/status
```

> `python ma_station.py` sans `--source` utilise le simulateur intégré de `korko.py`. Pour la démonstration graphique complète avec la page `:8080`, utiliser `--source localhost:8420`.

---

# URLs utiles

| URL | Fonction |
|---|---|
| `http://localhost:9000/` | application client |
| `http://localhost:9000/admin` | administration |
| `http://localhost:9000/parc` | état brut du parc |
| `http://localhost:8080/` | contrôle du simulateur |
| `http://localhost:9100/offline/status` | état local de la station |
| `http://localhost:9100/offline/locations` | locations locales hors ligne |

Pour utiliser l'application depuis un téléphone sur le même réseau, remplacer `localhost` par l'adresse IP locale de la machine exécutant KORKO, par exemple :

```text
http://192.168.1.42:9000
```

Le port `9000` et, pour le mode hors ligne, le port `9100` doivent être accessibles depuis le téléphone.

---

# Déroulé conseillé pour une démo

1. Ouvrir `http://localhost:9000/`.
2. Cliquer sur **Je veux surfer**.
3. Renseigner l'identité du client et choisir un moyen de paiement.
4. Réserver une planche.
5. Ouvrir `http://localhost:8080/`.
6. Faire partir physiquement la planche attribuée avec **Part surfer**.
7. Vérifier que l'application passe automatiquement en location active.
8. Faire revenir la planche avec **Revient**.
9. Vérifier le reçu, la durée et le montant.
10. Envoyer un rapport d'état.
11. Optionnel : envoyer une photo de la planche pour analyse.
12. Ouvrir `/admin` pour voir le journal, la maintenance, les transactions et l'état du parc.

### Démo accélérée de la limite de 23 h

Le simulateur permet d'augmenter fortement la vitesse. À vitesse `×60`, une heure simulée passe en environ une minute réelle.

Cela permet de montrer rapidement :

- fermeture des locations à 22 h ;
- saisie de la caution à 23 h ;
- retour tardif toujours reconnu ensuite.

---

# Simulateur

`korko_sim.py` propose trois modes :

- pilotage manuel ;
- scénarios préprogrammés ;
- rejeu de traces.

Scénarios disponibles :

| Scénario | Description |
|---|---|
| `depart` | départ franc |
| `sable` | planche posée sur le sable, sans vrai départ |
| `corps` | occlusion par un corps mouillé |
| `morte` | balise qui faiblit puis devient muette |
| `foule` | deux départs rapprochés et deux retours |
| `etrangere` | planche de la station B rapportée à A |
| `journee` | séquence complète avec plusieurs perturbations |

La page du simulateur permet également de modifier :

- vitesse ;
- bruit radio ;
- exposant de propagation ;
- pertes de paquets ;
- occlusion par un corps ;
- planche à l'envers ;
- balise muette ;
- arrivée d'une planche étrangère ;
- enregistrement et rejeu de traces.

Tests rapides du détecteur sans lancer le cloud :

```bash
python ma_station.py --sim --scenario depart
python ma_station.py --sim --scenario sable
python ma_station.py --sim --scenario journee
```

---

# Tester le mode hors ligne

1. Lancer cloud, simulateur et station.
2. Effectuer au moins une réservation en ligne depuis le navigateur afin que l'appareil reçoive son autorisation hors ligne.
3. Arrêter `mon_cloud.py` uniquement.
4. Revenir sur l'application.
5. L'interface doit afficher **Connexion limitée**.
6. Réserver une planche : la requête passe par la station locale sur `:9100`.
7. Simuler le départ et le retour.
8. Relancer le cloud.
9. Vérifier que le journal local est rejoué et que la location est reconstruite avec les timestamps originaux.

Voir `OFFLINE_MODE.md` pour les détails du protocole et les tests manuels complets.

---

# Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `OPENAI_API_KEY` | aucune | active l'analyse photo OpenAI |
| `KORKO_OFFLINE_SECRET` | secret prototype | signe les autorisations hors ligne ; même valeur cloud/station |
| `KORKO_CLOUD` | `http://localhost:9000/evenements` | URL du cloud utilisée par la station |
| `KORKO_OFFLINE_PORT` | `9100` | port de l'API locale station |
| `KORKO_CLOUD_DB` | `korko_cloud.sqlite3` | base SQLite du cloud |
| `KORKO_DB_PATH` | `korko-data/korko.sqlite3` | base SQLite de la file photo |
| `KORKO_STATION_JOURNAL` | `station_journal.ndjson` | journal durable station |
| `KORKO_STATION_STATE` | `station_offline_state.json` | état local durable station |

Exemple si le cloud est sur une autre machine :

```bash
export KORKO_CLOUD="http://192.168.1.50:9000/evenements"
python ma_station.py --source 192.168.1.51:8420
```

---

# API principales

## Cloud

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/api/arme` | réservation + caution |
| `POST` | `/api/annuler` | annulation de réservation |
| `GET` | `/api/client?identifiant=...` | état courant du client |
| `GET` | `/api/parc` | état du parc |
| `POST` | `/api/condition` | rapport d'état de la planche |
| `POST` | `/api/photo-verification` | dépôt d'une photo dans la file durable |
| `GET` | `/api/capabilities` | capacités du cloud, dont analyse photo |
| `POST` | `/api/reparer` | validation d'une réparation |
| `POST` | `/api/rebalancer` | confirmation d'un rééquilibrage |
| `POST` | `/api/admin/reset-demo` | reset de démonstration |
| `POST` | `/evenements` | réception des événements station |

## Station hors ligne

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/offline/status` | parc et locations locales |
| `GET` | `/offline/locations` | locations locales uniquement |
| `POST` | `/offline/arme` | réservation locale autorisée |

---

# Tests

Depuis la racine du projet, environnement virtuel activé :

```bash
python -m unittest -v \
  korko_test \
  paiement.test_paiement \
  paiement.test_sequestre \
  smart_contract.test_registre \
  tests.test_photo_queue \
  tests.test_photo_verification
```

Vérification syntaxique Python :

```bash
python -m py_compile \
  mon_cloud.py \
  ma_station.py \
  korko.py \
  korko_sim.py \
  photo_queue.py \
  photo_verification.py
```

Si Node.js est installé, vérification syntaxique du frontend :

```bash
node --check static/app.js
```

Vérification Git :

```bash
git diff --check
```

---

# Structure du projet

```text
korko-kit/
├── korko.py                    # contrat commun, flux et outils de détection
├── korko_sim.py                # simulateur physique + interface :8080
├── ma_station.py               # détection, journal, synchronisation, API offline
├── mon_cloud.py                # cloud, API client/admin et logique métier
│
├── static/
│   ├── index.html              # interface client
│   ├── app.js                  # logique frontend
│   └── style.css               # interface responsive
│
├── paiement/
│   ├── caisse.py               # orchestration réservation/paiement/caution
│   ├── prestataire.py          # carte et Apple Pay simulés
│   ├── clients.py              # fiches clients
│   ├── crypto.py               # paiements AVAX Fuji
│   ├── horaires.py             # règles 10 min / 22 h / 23 h
│   └── SequestreKorko.sol      # smart contract de caution
│
├── smart_contract/
│   ├── RegistreKorko.sol       # registre public des planches
│   ├── chaine.py               # connexion Avalanche / publication
│   ├── boite_envoi.py          # outbox blockchain durable
│   ├── parc.py                 # gestion du parc blockchain
│   └── registre_korko.json     # ABI + adresse du contrat déployé
│
├── photo_verification.py       # validation + appel OpenAI
├── photo_queue.py              # file photo SQLite durable
├── tests/                      # tests photo
├── test_images/                # images de test
├── OFFLINE_MODE.md             # documentation mode dégradé
└── DEBRIEF.md                  # résumé fonctionnel
```

---

# Sécurité avant publication GitHub

**À vérifier impérativement avant de rendre le dépôt public.**

Ces fichiers ne doivent jamais être publiés :

```text
smart_contract/.env
paiement/clients.json
```

Ajouter au minimum à `.gitignore` :

```gitignore
smart_contract/.env
paiement/clients.json
```

Si l'un de ces fichiers a déjà été ajouté à Git :

```bash
git rm --cached smart_contract/.env paiement/clients.json
```

Puis créer un commit supprimant ces fichiers du suivi Git.

> Si une véritable clé privée Avalanche ou une clé API a déjà été publiée dans un commit, la retirer du dernier commit ne suffit pas : il faut **révoquer/rotater la clé** et, si nécessaire, nettoyer l'historique Git.

Les bases SQLite, journaux de station, photos en attente et autres données d'exécution doivent également rester locales.

---

# Limites du prototype

- carte bancaire et Apple Pay simulés ;
- SMS simulés dans l'interface et le journal ;
- mode hors ligne basé sur HTTP local et secret HMAC partagé ;
- création de compte, rapport de condition et certaines opérations de paiement restent dépendants du cloud ;
- détection physique actuelle fondée sur un seuil RSSI et une durée de silence, donc sensible aux perturbations radio ;
- analyse photo informative uniquement ;
- Avalanche utilise le réseau de test Fuji et des AVAX de test ;
- le projet n'est pas prêt tel quel pour manipuler des paiements réels ou des données personnelles en production.

---

# Documentation complémentaire

- `OFFLINE_MODE.md` — fonctionnement détaillé du mode dégradé ;
- `paiement/README.md` — paiement, caution, règles horaires et crypto ;
- `smart_contract/README.md` — registre Avalanche Fuji et gestion du parc ;
- `DEBRIEF.md` — résumé fonctionnel rapide.

