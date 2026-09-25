"""
parc.py — gère le parc inscrit dans le contrat RegistreKorko.

    .venv\\Scripts\\python -m smart_contract.parc lister
    .venv\\Scripts\\python -m smart_contract.parc ajouter-station D
    .venv\\Scripts\\python -m smart_contract.parc retirer-station D
    .venv\\Scripts\\python -m smart_contract.parc ajouter-planche 7 A
    .venv\\Scripts\\python -m smart_contract.parc retirer-planche 7

Chaque modification est une transaction signée par le compte du cloud, et
reste visible sur l'explorateur. Le cloud relit le parc à son démarrage :
modifier le parc cloud arrêté, puis le relancer.
"""

import argparse
import sys

from web3.exceptions import ContractLogicError

from smart_contract import chaine


def lister(registre):
    """Affiche les stations, puis chaque planche et son état."""
    fonctions = registre.functions
    for code in fonctions.listerStations().call():
        station = fonctions.lireStation(code).call()
        print("station %s : %d planche(s)" % (code, station.nombrePlanches))
    for numero in sorted(fonctions.listerPlanches().call()):
        planche = fonctions.lirePlanche(numero).call()
        etat = "en mer" if planche.enMer else "au râtelier"
        print(
            "%s  base %s  %-11s  vue en %s  départs %d"
            % (
                chaine.balise(numero),
                planche.stationOrigine,
                etat,
                planche.derniereStation,
                planche.nombreDeparts,
            )
        )


def modifier(registre, appel):
    """Envoie la modification et affiche la preuve, ou le refus."""
    try:
        hash_transaction = appel.transact()
    except ContractLogicError as erreur:
        sys.exit("refus : %s" % chaine.nommer_refus(registre, erreur))
    chaine.attendre(registre.w3, hash_transaction)
    print("fait : %s" % chaine.lien(hash_transaction))


def lire_arguments():
    """Retourne la commande tapée et ses arguments."""
    analyseur = argparse.ArgumentParser(description="Parc KORKO sur la blockchain.")
    commandes = analyseur.add_subparsers(dest="commande", required=True)
    commandes.add_parser("lister")
    for nom in ("ajouter-station", "retirer-station"):
        commandes.add_parser(nom).add_argument("code")
    ajout = commandes.add_parser("ajouter-planche")
    ajout.add_argument("numero", type=int)
    ajout.add_argument("station")
    commandes.add_parser("retirer-planche").add_argument("numero", type=int)
    return analyseur.parse_args()


def appel_demande(fonctions, arguments):
    """Retourne l'appel au contrat qui correspond à la commande."""
    if arguments.commande == "ajouter-station":
        return fonctions.ajouterStation(arguments.code)
    if arguments.commande == "retirer-station":
        return fonctions.retirerStation(arguments.code)
    if arguments.commande == "ajouter-planche":
        return fonctions.ajouterPlanche(arguments.numero, arguments.station)
    return fonctions.retirerPlanche(arguments.numero)


def principal():
    """Exécute la commande tapée sur le registre de Fuji."""
    arguments = lire_arguments()
    registre = chaine.connecter()
    if arguments.commande == "lister":
        lister(registre)
    else:
        modifier(registre, appel_demande(registre.functions, arguments))


if __name__ == "__main__":
    principal()
