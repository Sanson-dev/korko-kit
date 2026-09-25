# paiement — identification, paiement et caution KORKO

Le client s'identifie une fois (prénom, nom, téléphone, moyen de paiement) ;
les fois suivantes, il réserve en un clic. Une caution de **300 €** est
bloquée à la réservation, jamais débitée si la planche revient avant 23 h.
Le client ne paie qu'à la fin : 0,20 €/min.

- **Carte bancaire et Apple Pay : simulés.** Le prestataire est construit
  comme un vrai (bloquer, débiter, libérer), mais aucun argent ne bouge.
- **Crypto : réelle, sur le réseau de test Avalanche Fuji.** La caution est
  versée dans le contrat de séquestre `SequestreKorko`, qui rend au client
  ce qu'il n'a pas dépensé.

## Règles
| Moment | Ce qui se passe |
|---|---|
| Réservation | Fiche client créée ou mise à jour, caution de 300 € bloquée, message « Votre planche 01 est réservée… avant 23 heures ». |
| 10 min sans départ | Réservation annulée, rien de débité, planche de nouveau disponible ; message « ⏱️ Temps écoulé… il vous suffit de refaire une réservation ». |
| Annulation | Possible tant que la planche n'est pas partie : rien n'est débité, la caution est libérée. |
| Départ | Message « Bonne session de surf… Vous avez retiré la planche 01 à 14:05… avant 23 heures ». |
| À partir de 22 h | Plus de nouvelle location. |
| Retour | Prix débité (0,20 €/min), caution libérée, message « Merci Camille ! Planche 01 rendue à 15:20… ». |
| 23 h sans retour | Caution de 300 € débitée, message au client. |

L'heure vient de l'horloge des stations (règle du kit) : date réelle sur une
vraie station ; dans le simulateur, t = 0 correspond à 9 h 00.

Les messages sont des SMS simulés : ils s'affichent dans l'appli du client et
dans le journal de `/admin`. Les passages entre `**` s'affichent en gras dans
l'appli.

## La crypto sur Fuji
- Contrat `SequestreKorko` :
  [`0x19118f120914576B30DcdBe32Ab23175C4CAB795`](https://testnet.snowtrace.io/address/0x19118f120914576B30DcdBe32Ab23175C4CAB795).
  Seul le compte du cloud peut régler une caution, et chaque caution ne se
  règle qu'une fois. Son code source est vérifié sur Snowtrace.
- Le client reçoit un **portefeuille de démo**, gardé dans sa fiche : il n'a
  rien à installer. Le compte du cloud l'approvisionne en AVAX de test, juste
  ce qui manque pour la caution et le gaz.
- Réservation : le portefeuille du client bloque la caution dans le contrat.
  Retour : le contrat paie la location à KORKO et **rend le reste au client**.
  23 h sans retour : KORKO saisit la caution.
- Taux de démo : 1 € = 0,000001 AVAX de test. Avec environ 0,0097 AVAX, le
  compte du cloud peut encore servir une trentaine de nouveaux clients crypto.
- Chaque étape est une vraie transaction : son lien Snowtrace s'affiche dans
  l'appli (« Preuve 1 sur Snowtrace ») et dans le journal (`CRYPTO …`).
- En production, on brancherait le portefeuille du client (Core, MetaMask) :
  le contrat ne change pas.

## Contenu
| Fichier | Rôle |
|---|---|
| `caisse.py` | Le paiement d'une location : réserver, annuler, terminer, débiter la caution, messages. |
| `prestataire.py` | Le prestataire : bloque, débite, libère (carte et Apple Pay simulés). |
| `crypto.py` | Le fil du séquestre : approvisionnement, blocage, règlement sur Fuji. |
| `SequestreKorko.sol` | Le contrat de séquestre des cautions crypto. |
| `clients.py` | Les fiches clients, gardées dans `clients.json` (jamais versionné). |
| `horaires.py` | L'heure du jour à partir de l'horloge des stations : 22 h, 23 h. |
| `deployer_sequestre.py` | Déploie le séquestre (déjà fait) et écrit `sequestre_korko.json`. |
| `test_paiement.py` | Tests des horaires, fiches, prestataire et caisse (sans réseau). |
| `test_sequestre.py` | Tests du contrat et du fil du séquestre, sur une blockchain locale. |

On ne garde jamais de numéro de carte ni de CVC : l'appli les vérifie (Luhn,
date, CVC) et n'envoie que la marque, les 4 derniers chiffres et la date
d'expiration, comme le ferait un vrai prestataire (Stripe.js). La fiche n'est
enregistrée qu'une fois la caution acceptée : une carte refusée ne devient pas
le moyen enregistré. Le moyen enregistré ne resert que depuis le téléphone du
client : taper le numéro de quelqu'un d'autre ne permet pas de payer avec sa
carte.

## Pour la démo
- Carte de test acceptée : `4242 4242 4242 4242`, `12/30`, `123`.
- Carte de test refusée : un numéro qui se termine par `0002`.
- Pour arriver à 23 h vite : vitesse ×60 dans le simulateur (1 h en 1 min).

## Commandes (depuis la racine du projet)
    .venv\Scripts\python -m unittest paiement.test_paiement -v
    .venv\Scripts\python -m unittest paiement.test_sequestre -v

## Branchement dans mon_cloud.py
    from paiement import caisse, horaires
    CAISSE = caisse.creer_caisse(note)

- `armer()` refuse après 22 h (`horaires.locations_ouvertes`), puis appelle
  `CAISSE.reserver(...)` avant de réserver la planche.
- `annuler()` (route `POST /api/annuler`) libère la planche et appelle
  `CAISSE.annuler(...)`, ou `CAISSE.expirer(...)` quand la planche n'a pas
  été prise dans les 10 minutes (`horaires.DELAI_RESERVATION`).
- Au départ de la planche, `traiter()` appelle `CAISSE.partir(...)`.
- `cloturer()` appelle `CAISSE.terminer(...)` au retour.
- `retards()` expire les réservations de plus de 10 minutes, puis appelle
  `CAISSE.saisir_caution(...)` à 23 h (`horaires.limite_retour` est retenue
  au départ de la planche). Il n'y a plus de rappel en cours de session.
- `traiter()` ignore un événement sans heure valide et reconnaît une relance
  du simulateur (son horloge repart à 9 h).
- `client_json()` ajoute `CAISSE.resume(...)` ; `/api/parc` ne renvoie plus
  les sessions (elles exposaient les numéros des clients) ; `/admin` montre les
  fiches clients et échappe son journal.
- Le registre et le séquestre signent avec le même compte du cloud :
  `chaine.VERROU_CLOUD` leur fait envoyer une transaction à la fois.
- Une réservation faite à la station pendant une coupure du cloud (mode hors
  ligne, voir `OFFLINE_MODE.md`) est rejouée par `reserver_hors_ligne()` : la
  caution est alors bloquée sur le moyen enregistré de ce téléphone
  (`Fichier.par_appareil`).
- L'état du cloud est sauvegardé dans `korko_cloud.sqlite3` : les
  autorisations y sont écrites comme des dictionnaires, et
  `Autorisation.depuis(...)` les recrée au démarrage. « Réinitialiser la
  démo » (`/admin`) libère les cautions encore bloquées.
