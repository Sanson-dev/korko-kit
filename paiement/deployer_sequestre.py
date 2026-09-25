"""
deployer_sequestre.py — déploie le contrat SequestreKorko sur Fuji, une fois.

    .venv\\Scripts\\python -m paiement.deployer_sequestre

Compile SequestreKorko.sol et le déploie avec le compte du cloud, qui devient
`korko`, seul à régler les cautions. Écrit aussitôt son adresse et son ABI
dans sequestre_korko.json, que le cloud lit au démarrage.
"""

import json
import os
import sys

import solcx

from paiement import crypto
from smart_contract import chaine
from smart_contract.deployer import VERSION_EVM, VERSION_SOLC, deployer

SOURCE = "SequestreKorko.sol"


def compiler():
    """Retourne l'ABI et le bytecode de SequestreKorko."""
    solcx.install_solc(VERSION_SOLC)
    chemin = os.path.join(crypto.DOSSIER, SOURCE)
    sortie = solcx.compile_files([chemin], output_values=["abi", "bin"],
                                 solc_version=VERSION_SOLC,
                                 evm_version=VERSION_EVM, optimize=True,
                                 base_path=crypto.DOSSIER)
    contrat = sortie["%s:SequestreKorko" % SOURCE]
    return contrat["abi"], contrat["bin"]


def sauvegarder(sequestre):
    """Écrit l'adresse et l'ABI du contrat déployé."""
    with open(crypto.FICHIER_SEQUESTRE, "w", encoding="utf-8") as fichier:
        json.dump({"adresse": sequestre.address, "abi": sequestre.abi},
                  fichier, indent=1)


def principal():
    """Déploie le séquestre, sauf s'il l'est déjà, et note son adresse."""
    if os.path.exists(crypto.FICHIER_SEQUESTRE):
        sys.exit("déjà déployé : %s existe" % crypto.FICHIER_SEQUESTRE)
    sequestre = deployer(chaine.connecter_fuji(), *compiler())
    sauvegarder(sequestre)
    print("SequestreKorko : %s" % chaine.lien_adresse(sequestre.address))


if __name__ == "__main__":
    principal()
