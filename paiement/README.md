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
| Réservation | Fiche client créée ou mise à jour, caution de 300 € bloquée, message « Bonne session de surf… avant 23 h ». |
| À partir de 22 h | Plus de nouvelle location. |
| Retour | Prix débité (0,20 €/min), caution libérée, message de remerciement. |
| 23 h sans retour | Caution de 300 € débitée, message au client. |

L'heure vient de l'horloge des stations (règle du kit) : date réelle sur une
vraie station ; dans le simulateur, t = 0 correspond à 9 h 00.

Les messages sont des SMS simulés : ils s'affichent dans l'appli du client et
dans le journal de `/admin`.

## Contenu
| Fichier | Rôle |
|---|---|
| `caisse.py` | Le paiement d'une location : réserver, terminer, débiter la caution, messages. |
| `prestataire.py` | Le prestataire : bloque, débite, libère (carte et Apple Pay simulés). |
| `clients.py` | Les fiches clients, gardées dans `clients.json` (jamais versionné). |
| `horaires.py` | L'heure du jour à partir de l'horloge des stations : 22 h, 23 h. |
| `test_paiement.py` | Tests des horaires, fiches, prestataire et caisse. |

On ne garde jamais de numéro de carte ni de CVC : l'appli les vérifie (Luhn,
date, CVC) et n'envoie que la marque, les 4 derniers chiffres et la date
d'expiration, comme le ferait un vrai prestataire (Stripe.js).

## Pour la démo
- Carte de test acceptée : `4242 4242 4242 4242`, `12/30`, `123`.
- Carte de test refusée : un numéro qui se termine par `0002`.
- Pour arriver à 23 h vite : vitesse ×60 dans le simulateur (1 h en 1 min).

## Commandes (depuis la racine du projet)
    .venv\Scripts\python -m unittest paiement.test_paiement -v

## Branchement dans mon_cloud.py
    from paiement import caisse, horaires
    CAISSE = caisse.creer_caisse(note)

- `armer()` refuse après 22 h (`horaires.locations_ouvertes`), puis appelle
  `CAISSE.reserver(...)` avant de réserver la planche.
- `cloturer()` appelle `CAISSE.terminer(...)` au retour.
- `retards()` appelle `CAISSE.saisir_caution(...)` à 23 h (`horaires.limite_retour`
  est retenue au départ de la planche).
- `client_json()` ajoute `CAISSE.resume(...)` ; `/api/parc` ne renvoie plus
  les sessions (elles exposaient les numéros des clients) ; `/admin` montre les
  fiches clients et échappe son journal.
