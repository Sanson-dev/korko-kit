"""
horaires.py — l'heure de la journée, lue dans l'horloge des stations.

Le temps vient toujours du champ `t` des stations, jamais de l'horloge du
PC : c'est la règle du kit. Une vraie station donne une date Unix ; le
simulateur compte les secondes depuis son lancement, qu'on fait commencer
à 9 h du matin.
"""

import time

DEBUT_SIMULATION = 9 * 3600      # le simulateur démarre à 9 h 00
FERMETURE = 22 * 3600            # plus de nouvelle location à partir de 22 h
LIMITE_RETOUR = 23 * 3600        # planche à rendre avant 23 h
JOUR = 24 * 3600
DATE_UNIX_MINIMALE = 10 ** 9     # en dessous, t vient du simulateur


def seconde_du_jour(t):
    """Retourne le nombre de secondes écoulées depuis minuit."""
    if t < DATE_UNIX_MINIMALE:
        return (DEBUT_SIMULATION + t) % JOUR
    heure = time.localtime(t)
    return heure.tm_hour * 3600 + heure.tm_min * 60 + heure.tm_sec


def texte_heure(t):
    """Retourne l'heure au format « HH:MM »."""
    seconde = int(seconde_du_jour(t))
    return "%02d:%02d" % (seconde // 3600, seconde % 3600 // 60)


def locations_ouvertes(t):
    """Vrai tant qu'on peut encore louer : avant 22 h."""
    return seconde_du_jour(t) < FERMETURE


def limite_retour(t):
    """Retourne le t du prochain 23 h 00 : l'heure limite de retour."""
    return t + (LIMITE_RETOUR - seconde_du_jour(t)) % JOUR
