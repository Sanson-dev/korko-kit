# smart_contract — le registre KORKO sur Avalanche

Le client n'a pas à croire KORKO sur parole. Chaque planche est un objet
unique d'un contrat public, et chacun de ses départs et retours y est inscrit :
personne, pas même KORKO, ne peut réécrire cet historique.

- Réseau : Avalanche **Fuji** (réseau de test, chain id 43113)
- Contrat : [`0xeb267Fd27cc6222178a3CbF2DadA98d6bd62D2F9`](https://testnet.snowtrace.io/address/0xeb267Fd27cc6222178a3CbF2DadA98d6bd62D2F9)
- Écriture réservée au compte du cloud : `0xaB75b88e66DE5B217F8Ac9F89914d827b8Fb5bf8`
- Code source vérifié sur Snowtrace (solc 0.8.24, optimiseur 200, EVM
  shanghai) : les événements s'y lisent en clair.

## Contenu

| Fichier | Rôle |
|---|---|
| `RegistreKorko.sol` | Le contrat : stations, planches, départs, retours. |
| `chaine.py` | Le lien cloud → Fuji : lit le parc, inscrit les événements en tâche de fond. |
| `deployer.py` | Déploie le contrat une fois, puis inscrit le parc de `korko.STATIONS`. |
| `parc.py` | Liste, ajoute ou retire des stations et des planches. |
| `test_registre.py` | 14 tests sur une blockchain locale, sans réseau ni tokens. |
| `registre_korko.json` | Adresse et ABI du contrat déployé (public, à versionner). |
| `.env` | Clé privée du cloud : **jamais versionnée** (voir `.gitignore`). |

## Installation (Windows, depuis la racine du projet)

    py -m venv .venv
    .venv\Scripts\python -m pip install -r smart_contract\requirements.txt

`.env` contient une seule ligne : `KORKO_CLE_PRIVEE=0x…`. Un coéquipier sans
la clé du cloud peut y mettre une clé jetable : tout marche, mais le contrat
refuse ses inscriptions (`NonAutorise`), ce qu'affiche le journal.

## Commandes (depuis la racine du projet)

    .venv\Scripts\python -m unittest smart_contract.test_registre -v
    .venv\Scripts\python -m smart_contract.deployer
    .venv\Scripts\python -m smart_contract.parc lister
    .venv\Scripts\python -m smart_contract.parc ajouter-planche 7 A
    .venv\Scripts\python -m smart_contract.parc retirer-planche 7
    .venv\Scripts\python -m smart_contract.parc ajouter-station D
    .venv\Scripts\python -m smart_contract.parc retirer-station D

Le cloud se lance avec le Python de `.venv` : `.venv\Scripts\python mon_cloud.py`.
`deployer.py` ne sert qu'une fois : le contrat est déjà déployé.

## Branchement dans mon_cloud.py

Les seules lignes ajoutées au cloud :

    from korko import STATIONS
    from smart_contract import chaine

    REGISTRE = chaine.connecter()
    PARC = chaine.lire_parc(REGISTRE, STATIONS)
    PUBLIEUR = chaine.Publieur(REGISTRE, chaine.compte_du_cloud(), note)

    # dans traiter(), juste après le contrôle « balise inconnue »
    if type_ in ("DEPART", "RETOUR", "ETRANGERE"):
        PUBLIEUR.publier(ev)

    # au lancement
    PUBLIEUR.start()

Chaque inscription laisse dans le journal de `/admin` une ligne `CHAÎNE` avec
le lien Snowtrace de sa transaction.

## Règles du registre

- Une planche = le numéro de sa balise (`korko-07` → 7), unique dans le parc,
  rattachée à sa station d'origine.
- **Tous** les départs et retours reçus des stations sont inscrits, même ceux
  que le cloud ignore pour la facturation. Un retour dans une autre station
  (`ETRANGERE`) s'inscrit comme un retour, avec la station où la planche est rangée.
- Seules une planche ou une station inconnues sont refusées.
- On ne ferme une station qu'une fois vide : plus aucune de ses planches, et
  aucune planche d'une autre station rangée chez elle.
- Une planche peut être retirée même en mer (perdue, volée) ; son historique
  reste lisible.
- `instantMs` est l'horloge de la station : date Unix sur une vraie station,
  temps de simulation en démo. La date réelle est celle du bloc.

## Ce qu'il faut savoir

- **Aucune donnée client** sur la chaîne : ni téléphone ni identifiant.
  L'historique suit les planches, pas les personnes.
- Le registre atteste ce que le cloud a reçu des stations : il empêche de
  réécrire l'histoire, pas de mal observer (la qualité de la détection compte).
- Les événements pas encore inscrits attendent en mémoire : avant d'arrêter
  le cloud, attendre la dernière ligne `CHAÎNE` du journal.
- Le cloud lit le parc au démarrage : modifier le parc avec `parc.py`, puis
  relancer le cloud. Si Fuji ne répond pas au démarrage, le cloud démarre
  avec `korko.STATIONS` et inscrira les événements dès que Fuji répondra.
