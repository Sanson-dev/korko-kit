"""
deployer.py — déploie le contrat RegistreKorko sur Fuji, une seule fois.

    .venv\\Scripts\\python -m smart_contract.deployer

Compile RegistreKorko.sol, le déploie avec le compte du cloud, écrit son
adresse et son ABI dans registre_korko.json (que le cloud lit au
démarrage), puis y inscrit le parc de départ, korko.STATIONS. Si une
inscription échoue en route, compléter le parc avec parc.py.
"""

import json
import os
import sys

import solcx

from korko import STATIONS
from smart_contract import chaine

VERSION_SOLC = "0.8.24"
VERSION_EVM = "shanghai"
SOURCE = os.path.join(chaine.DOSSIER, "RegistreKorko.sol")


def compiler():
    """Retourne l'ABI et le bytecode de RegistreKorko."""
    solcx.install_solc(VERSION_SOLC)
    sortie = solcx.compile_files(
        [SOURCE],
        output_values=["abi", "bin"],
        solc_version=VERSION_SOLC,
        evm_version=VERSION_EVM,
        optimize=True,
        base_path=chaine.DOSSIER,
    )
    contrat = next(iter(sortie.values()))
    return contrat["abi"], contrat["bin"]


def deployer(w3, abi, bytecode):
    """Déploie le contrat ; le compte qui déploie en devient propriétaire."""
    fabrique = w3.eth.contract(abi=abi, bytecode=bytecode)
    recu = chaine.attendre(w3, fabrique.constructor().transact())
    return w3.eth.contract(address=recu.contractAddress, abi=abi, decode_tuples=True)


def inscrire_parc(registre, stations):
    """Inscrit chaque station, puis chacune de ses planches."""
    fonctions = registre.functions
    for code, balises in sorted(stations.items()):
        chaine.attendre(registre.w3, fonctions.ajouterStation(code).transact())
        for balise in sorted(balises):
            appel = fonctions.ajouterPlanche(chaine.numero(balise), code)
            chaine.attendre(registre.w3, appel.transact())


def sauvegarder(registre):
    """Écrit l'adresse et l'ABI du contrat déployé."""
    with open(chaine.FICHIER_REGISTRE, "w", encoding="utf-8") as fichier:
        json.dump({"adresse": registre.address, "abi": registre.abi}, fichier, indent=1)


def principal():
    """Déploie, sauvegarde l'adresse, puis inscrit le parc de départ."""
    if os.path.exists(chaine.FICHIER_REGISTRE):
        sys.exit("déjà déployé : %s existe" % chaine.FICHIER_REGISTRE)
    registre = deployer(chaine.connecter_fuji(), *compiler())
    sauvegarder(registre)
    print("RegistreKorko : %s" % chaine.lien_adresse(registre.address))
    inscrire_parc(registre, STATIONS)
    print("parc inscrit : %d stations" % len(STATIONS))


if __name__ == "__main__":
    principal()
