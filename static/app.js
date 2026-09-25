/*
 * KORKO : parcours du client sur son téléphone.
 * prêt (« Je veux surfer ») -> identification et paiement
 * -> planche réservée (ou annulée) -> session en cours
 * -> reçu (ou caution débitée après 23 h).
 * Si le cloud ne répond plus, la station prend le relais (« Connexion
 * limitée ») pour un client déjà autorisé lors d'une location en ligne.
 * Le serveur ne reçoit jamais le numéro complet de la carte ni son CVC.
 */

const STATION = 'A';
const TARIF_MINUTE = 0.2;
const INTERVALLE_SONDAGE = 1200;
const DELAI_SONDAGE = 6000;
const ADRESSE_STATION = 'http://' + window.location.hostname + ':9100';
const CLE_APPAREIL = 'korko-session';
const CLE_CLIENT = 'korko-client';
const CLE_GENERATION = 'korko-generation';
const CLE_AUTORISATION = 'korko-offline-authorization';
const CLE_RESERVATION_LOCALE = 'korko-reservation-locale';
const CLE_SESSION_EN_LIGNE = 'korko-session-en-ligne';
const CLE_SESSIONS_FERMEES = 'korko-dismissed-session';
const ETATS_CLOS = ['retournée', 'caution débitée', 'annulée'];
const MESSAGE_RESEAU = 'Connexion impossible. Vérifiez le réseau et réessayez.';
const MESSAGE_STATION = 'La station ne répond pas. Réessayez dans un instant.';
const MESSAGE_REINITIALISATION = 'La démonstration a été réinitialisée.';
const MESSAGE_RETOUR_STATION = 'Réservation impossible sans connexion. Revenez '
  + 'à l’accueil pour prendre une planche à la station.';
/* Refus du cloud qui concernent le moyen de paiement (pas l'horaire ni le parc). */
const REFUS_DU_MOYEN = /paiement|carte/i;
const ETATS_EN_LOCATION = ['armée', 'en cours'];
const TEXTE_LOCATION = 'La station continue de suivre votre planche. Tout sera '
  + 'synchronisé dès le retour de la connexion.';
const TEXTE_CLOTURE = 'Le service en ligne ne répond pas. Tout sera mis à jour '
  + 'dès le retour de la connexion.';
/* Texte du bandeau « Connexion limitée » : par écran, et trois cas sur l'accueil. */
const TEXTES_HORS_LIGNE = {
  autorise: 'Le service en ligne ne répond pas. Vous pouvez tout de même '
    + 'prendre une planche à la station.',
  inconnu: 'Le service en ligne ne répond pas. Une première location en '
    + 'ligne est nécessaire pour louer hors ligne.',
  muette: 'Le service en ligne et la station ne répondent pas. Réessayez '
    + 'dans un instant.',
  identification: 'Le service en ligne ne répond pas. Réessayez dans un instant.',
  reservee: TEXTE_LOCATION,
  'en-cours': TEXTE_LOCATION,
  recu: TEXTE_CLOTURE,
  'caution-debitee': TEXTE_CLOTURE,
};
const CARTE_DE_TEST = {
  'numero-carte': '4242 4242 4242 4242',
  expiration: '12/30',
  cvc: '123',
};
const REGLES_IDENTITE = [
  ['prenom', estRempli, 'Indiquez votre prénom.'],
  ['nom', estRempli, 'Indiquez votre nom.'],
  ['telephone', telephoneValide, 'Numéro de téléphone invalide.'],
];
const REGLES_CARTE = [
  ['numero-carte', numeroCarteValide, 'Numéro de carte invalide.'],
  ['expiration', expirationValide, 'Date d’expiration invalide ou passée.'],
  ['cvc', cvcValide, 'Le CVC comporte 3 chiffres.'],
];
const ICONES = {
  carte: '#icone-carte',
  apple_pay: '#icone-apple-pay',
  crypto: '#icone-avalanche',
};
const AFFICHAGES = {
  'armée': afficherReservee,
  'en cours': afficherEnCours,
  'retournée': afficherRecu,
  'caution débitée': afficherCautionDebitee,
};
const AFFICHAGES_LOCAUX = {
  ARMEE: afficherReserveeLocale,
  EN_COURS: afficherEnCoursLocal,
  TERMINEE: afficherRecuLocal,
};
const vue = {
  identifiant: identifiantAppareil(),
  generation: lireJson(CLE_GENERATION),
  epoque: 0,
  horsLigne: false,
  stationMuette: false,
  sondage: null,
  parc: null,
  sondageEnAttente: false,
  parcEnAttente: false,
  priseEnCours: false,
  moyenManuel: false,
  sessionRecu: null,
  sessionPassee: null,
  conditionChoisie: '',
  envoiCondition: false,
};

/* ---------- Outils ---------- */

function element(id) {
  return document.getElementById(id);
}

function chiffres(texte) {
  return String(texte || '').replace(/\D/g, '');
}

function numeroPlanche(balise) {
  return String(balise || '').replace('korko-', '');
}

function formaterEuros(montant, decimales = 2) {
  const texte = Number(montant || 0).toLocaleString('fr-FR', {
    minimumFractionDigits: decimales,
    maximumFractionDigits: decimales,
  });
  return texte + ' €';
}

function formaterDuree(secondes) {
  const total = Math.max(0, Math.floor(secondes || 0));
  const minutes = String(Math.floor(total / 60)).padStart(2, '0');
  return minutes + ':' + String(total % 60).padStart(2, '0');
}

function prixEstime(secondes) {
  return secondes / 60 * TARIF_MINUTE;
}

/*
 * Découpe un message : les passages entre ** deviennent du gras, le reste
 * reste du texte brut, jamais interprété comme du HTML.
 */
function texteEnrichi(texte) {
  return String(texte || '').split('**').map((morceau, rang) => {
    if (rang % 2 === 0) return morceau;
    const gras = document.createElement('strong');
    gras.textContent = morceau;
    return gras;
  });
}

/* ---------- Mémoire de l'appareil ---------- */

function lireMemoire(cle) {
  try {
    return localStorage.getItem(cle);
  } catch {
    return null;
  }
}

function ecrireMemoire(cle, valeur) {
  try {
    localStorage.setItem(cle, valeur);
  } catch {
    // Stockage bloqué (navigation privée) : l'appli fonctionne sans.
  }
}

function effacerMemoire(cle) {
  try {
    localStorage.removeItem(cle);
  } catch {
    // Rien à effacer si le stockage est bloqué.
  }
}

function lireJson(cle) {
  try {
    return JSON.parse(lireMemoire(cle));
  } catch {
    return null;
  }
}

function clientMemorise() {
  return lireJson(CLE_CLIENT);
}

/* Un reçu ou une annulation déjà vus ne reviennent pas au rechargement. */
function sessionFermee({etat, session_id: session}) {
  const fermees = lireJson(CLE_SESSIONS_FERMEES) || [];
  return ETATS_CLOS.includes(etat) && fermees.includes(session);
}

function fermerSession(session) {
  const fermees = lireJson(CLE_SESSIONS_FERMEES) || [];
  if (!session || fermees.includes(session)) return;
  const dernieres = [...fermees, session].slice(-20);
  ecrireMemoire(CLE_SESSIONS_FERMEES, JSON.stringify(dernieres));
}

function nouvelIdentifiant() {
  const octets = crypto.getRandomValues(new Uint8Array(16));
  const identifiant = Array.from(
    octets, octet => octet.toString(16).padStart(2, '0')).join('');
  ecrireMemoire(CLE_APPAREIL, identifiant);
  return identifiant;
}

function identifiantAppareil() {
  return lireMemoire(CLE_APPAREIL) || nouvelIdentifiant();
}

/* Prénom, nom et téléphone restent pour pré-remplir le formulaire. */
function oublierPaiementMemorise() {
  const client = clientMemorise();
  if (!client) return;
  delete client.paiement;
  ecrireMemoire(CLE_CLIENT, JSON.stringify(client));
}

function enLocation(etat) {
  return ETATS_EN_LOCATION.includes(etat);
}

/*
 * Dernier état connu d'une location en ligne en cours (sans le jeton) : si le
 * cloud tombe, il reste affiché, figé, et bloque une prise à la station.
 */
function retenirSessionEnLigne(donnees) {
  if (!enLocation(donnees.etat)) return effacerMemoire(CLE_SESSION_EN_LIGNE);
  const sansJeton = {...donnees, offline_authorization: undefined};
  ecrireMemoire(CLE_SESSION_EN_LIGNE, JSON.stringify(sansJeton));
}

function sessionEnLigneActive() {
  return lireJson(CLE_SESSION_EN_LIGNE);
}

/* ---------- Autorisation hors ligne et location prise à la station ---------- */

/* Jeton « charge.signature » : seule la charge (JSON en base64url) est lue. */
function lireChargeJeton(jeton) {
  const charge = String(jeton).split('.')[0]
    .replace(/-/g, '+').replace(/_/g, '/');
  const complete = charge.padEnd(Math.ceil(charge.length / 4) * 4, '=');
  return JSON.parse(atob(complete));
}

/* La session en ligne qui a fourni le jeton sert à repérer un cloud en retard. */
function memoriserAutorisation(jeton, sessionId) {
  try {
    const charge = lireChargeJeton(jeton);
    ecrireMemoire(CLE_AUTORISATION, JSON.stringify({
      token: jeton,
      authorization_id: charge.authorization_id,
      station_id: charge.station_id,
      expires: charge.expires,
      session_id: sessionId || null,
    }));
  } catch {
    // Jeton illisible : la location hors ligne reste simplement indisponible.
  }
}

function autorisationValide() {
  const autorisation = lireJson(CLE_AUTORISATION);
  const valide = Boolean(autorisation && autorisation.token)
    && autorisation.station_id === STATION
    && autorisation.expires > Date.now() / 1000;
  return valide ? autorisation : null;
}

function reservationLocale() {
  return lireJson(CLE_RESERVATION_LOCALE);
}

function memoriserReservationLocale(reservation) {
  ecrireMemoire(CLE_RESERVATION_LOCALE, JSON.stringify(reservation));
}

function oublierReservationLocale() {
  effacerMemoire(CLE_RESERVATION_LOCALE);
}

function retenirStatutLocal(statut) {
  const reservation = reservationLocale();
  if (!reservation || reservation.statut === statut) return;
  memoriserReservationLocale({...reservation, statut});
}

/* Une fois le reçu hors ligne quitté, la location locale est oubliée. */
function oublierLocationTerminee() {
  const reservation = reservationLocale();
  if (reservation && reservation.statut === 'TERMINEE') {
    oublierReservationLocale();
  }
}

/* ---------- Échanges avec le cloud et la station ---------- */

function optionsRequete(corps, delai) {
  const options = delai && AbortSignal.timeout
    ? {signal: AbortSignal.timeout(delai)} : {};
  if (!corps) return options;
  return {
    ...options,
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(corps),
  };
}

function erreurMarquee(message, marque) {
  const erreur = new Error(message);
  erreur[marque] = true;
  return erreur;
}

/* Sans réponse JSON (réseau coupé, délai dépassé, erreur 5xx) : null. */
async function requeteJson(url, corps, delai) {
  let reponse;
  try {
    reponse = await fetch(url, optionsRequete(corps, delai));
  } catch {
    return null;
  }
  const donnees = await reponse.json().catch(() => null);
  if (!donnees || reponse.status >= 500) return null;
  return {ok: reponse.ok, donnees};
}

/*
 * Erreurs marquées : « reseau » quand le cloud ne répond pas, « ignoree »
 * quand la réponse est périmée (démo réinitialisée entre-temps).
 */
async function appelerApi(url, corps) {
  const epoque = vue.epoque;
  const resultat = await requeteJson(url, corps, corps ? 0 : DELAI_SONDAGE);
  if (epoque !== vue.epoque) throw erreurMarquee('', 'ignoree');
  basculerHorsLigne(!resultat);
  if (!resultat) throw erreurMarquee(MESSAGE_RESEAU, 'reseau');
  const {ok, donnees} = resultat;
  if (demoReinitialisee(donnees.generation)) {
    reinitialiserDemo();
    throw erreurMarquee(MESSAGE_REINITIALISATION, 'ignoree');
  }
  if (!ok) throw new Error(donnees.erreur || MESSAGE_RESEAU);
  return donnees;
}

async function appelerStation(chemin, corps) {
  const resultat = await requeteJson(ADRESSE_STATION + chemin, corps,
    DELAI_SONDAGE);
  if (!resultat) throw erreurMarquee(MESSAGE_STATION, 'reseau');
  if (!resultat.ok) throw new Error(resultat.donnees.erreur || MESSAGE_STATION);
  return resultat.donnees;
}

function demarrerSondage() {
  arreterSondage();
  vue.sondage = setInterval(rafraichir, INTERVALLE_SONDAGE);
  rafraichir();
}

function arreterSondage() {
  clearInterval(vue.sondage);
  vue.sondage = null;
}

/* Un seul sondage à la fois : une réponse lente ne double pas la suivante. */
async function rafraichir() {
  if (vue.sondageEnAttente) return;
  vue.sondageEnAttente = true;
  try {
    await suivreClient();
  } finally {
    vue.sondageEnAttente = false;
  }
}

async function suivreClient() {
  const url = '/api/client?identifiant=' + encodeURIComponent(vue.identifiant);
  let donnees;
  try {
    donnees = await appelerApi(url);
  } catch (erreur) {
    if (erreur.reseau) await suivreHorsLigne();
    return;
  }
  if (!vue.sondage) return;
  if (cloudEnRetard(donnees)) return suivreReservationLocale();
  afficherClient(donnees);
}

/* Cloud muet : la station suit la location locale ; une location en ligne reste figée. */
async function suivreHorsLigne() {
  if (reservationLocale()) return suivreReservationLocale();
  const session = sessionEnLigneActive();
  if (session && vue.sondage) afficherClient(session);
}

/*
 * Une location prise hors ligne n'arrive au cloud qu'une fois le journal de
 * la station rejoué : d'ici là, l'ancienne session (ou « inconnue ») que
 * renvoie le cloud est ignorée et la station fait foi.
 */
function cloudEnRetard(donnees) {
  const reservation = reservationLocale();
  if (!reservation) return false;
  const aJour = cloudAJour(donnees, reservation);
  if (aJour) oublierReservationLocale();
  return !aJour;
}

/*
 * À jour : le cloud a reçu la location locale ou connaît une session plus
 * récente. Si la session précédente est encore en location, le cloud ignore
 * la location locale (une seule à la fois) : c'est lui qui fait foi.
 */
function cloudAJour({session_id: sessionCloud, etat}, reservation) {
  if (sessionCloud === reservation.id) return true;
  if (!sessionCloud) return false;
  return sessionCloud !== reservation.sessionPrecedente || enLocation(etat);
}

/* ---------- Réinitialisation de la démonstration ---------- */

/* Vrai si l'admin a réinitialisé la démo depuis la dernière réponse connue. */
function demoReinitialisee(recue) {
  if (recue === undefined || recue === null) return false;
  if (recue === vue.generation) return false;
  const connue = vue.generation;
  vue.generation = recue;
  ecrireMemoire(CLE_GENERATION, JSON.stringify(recue));
  return connue !== null;
}

/*
 * Nouvel appareil pour le cloud : il refuserait le moyen enregistré sous
 * l'ancien identifiant, qui est donc oublié lui aussi (la tuile « enregistré »
 * revient après la première location).
 */
function oublierSessionDeDemo() {
  effacerMemoire(CLE_AUTORISATION);
  effacerMemoire(CLE_SESSION_EN_LIGNE);
  oublierReservationLocale();
  oublierPaiementMemorise();
  vue.identifiant = nouvelIdentifiant();
}

/* On oublie la session locale et on revient sur « Je veux surfer ». */
function reinitialiserDemo() {
  vue.epoque += 1;
  arreterSondage();
  oublierSessionDeDemo();
  vue.sessionRecu = null;
  vue.sessionPassee = null;
  const feuille = element('feuille-apple-pay');
  if (feuille.open) feuille.close();
  naviguer('pret');
  afficherAnnonce(MESSAGE_REINITIALISATION);
}

/* ---------- Connexion limitée ---------- */

function basculerHorsLigne(horsLigne) {
  if (vue.horsLigne === horsLigne) return;
  vue.horsLigne = horsLigne;
  element('bandeau-hors-ligne').hidden = !horsLigne;
  element('bouton-surfer').hidden = horsLigne;
  element('bouton-hors-ligne').hidden = !horsLigne;
  element('bouton-hors-ligne').disabled = true;
  element('erreur-hors-ligne').textContent = '';
  actualiserBandeau();
}

function ecranActif() {
  return document.querySelector('.screen.active').id;
}

function texteBandeau() {
  const ecran = ecranActif();
  if (ecran !== 'pret') return TEXTES_HORS_LIGNE[ecran];
  if (sessionEnLigneActive()) return TEXTE_LOCATION;
  if (!autorisationValide()) return TEXTES_HORS_LIGNE.inconnu;
  return vue.stationMuette ? TEXTES_HORS_LIGNE.muette : TEXTES_HORS_LIGNE.autorise;
}

function actualiserBandeau() {
  if (vue.horsLigne) element('texte-hors-ligne').textContent = texteBandeau();
}

async function chargerParcLocal() {
  const statut = await appelerStation('/offline/status').catch(() => null);
  if (vue.horsLigne) afficherParcLocal(statut);
}

/* Station muette : le bandeau le dit, les horaires restent les mêmes. */
function afficherParcLocal(statut) {
  const libres = statut ? (statut.planches_disponibles || []).length : null;
  vue.stationMuette = !statut;
  afficherNombrePlanches(libres);
  element('bouton-hors-ligne').disabled = !priseHorsLignePossible(libres);
  afficherHoraires(libres !== 0, texteHoraires(true, libres !== 0));
  actualiserBandeau();
}

/* Une seule location à la fois : pas de prise si une location en ligne court. */
function priseHorsLignePossible(libres) {
  return !vue.priseEnCours && Boolean(autorisationValide())
    && !sessionEnLigneActive() && libres > 0;
}

function basculerPriseHorsLigne(enCours) {
  vue.priseEnCours = enCours;
  element('bouton-hors-ligne').disabled = enCours;
  element('texte-prendre').textContent =
    enCours ? 'Réservation…' : 'Prendre une planche';
}

async function prendrePlancheHorsLigne() {
  const autorisation = autorisationValide();
  if (!autorisation || vue.priseEnCours) return;
  const corps = {authorization: autorisation.token, station: STATION};
  basculerPriseHorsLigne(true);
  element('erreur-hors-ligne').textContent = '';
  try {
    const donnees = await appelerStation('/offline/arme', corps);
    apresPriseHorsLigne(donnees, autorisation);
  } catch (erreur) {
    element('erreur-hors-ligne').textContent = erreur.message;
  } finally {
    basculerPriseHorsLigne(false);
  }
}

function apresPriseHorsLigne(donnees, autorisation) {
  memoriserReservationLocale({
    id: donnees.offline_reservation_id,
    balise: donnees.balise,
    sessionPrecedente: autorisation.session_id || null,
    statut: 'ARMEE',
  });
  afficherLocationLocale({status: 'ARMEE', balise: donnees.balise});
  demarrerSondage();
}

async function suivreReservationLocale() {
  const reservation = reservationLocale();
  if (!reservation) return;
  const statut = await appelerStation('/offline/status').catch(() => null);
  const location = statut && (statut.locations || []).find(
    candidate => candidate.offline_reservation_id === reservation.id);
  const courante = reservationLocale();
  if (!location || !vue.sondage || !courante) return;
  if (courante.id === reservation.id) afficherLocationLocale(location, statut.t);
}

function afficherLocationLocale(location, instant) {
  const afficher = AFFICHAGES_LOCAUX[location.status];
  if (!afficher) return;
  retenirStatutLocal(location.status);
  element('suivi').hidden = true;
  afficher(location, instant);
}

/* Pas d'annulation hors ligne : la station ne sait que réserver. */
function afficherReserveeLocale(location) {
  element('planche-reservee').textContent = numeroPlanche(location.balise);
  proposerAnnulation(false);
  element('erreur-annulation').textContent = '';
  afficherEcran('reservee');
}

/*
 * Une réservation prise à la station ne s'annule pas, même une fois connue du
 * cloud : la station n'en saurait rien et garderait la planche bloquée.
 */
function proposerAnnulation(possible) {
  element('annuler-reservation').hidden = !possible;
  element('note-sans-annulation').hidden = possible;
}

function afficherEnCoursLocal(location, instant) {
  const duree = Math.max(0, (instant || 0) - (location.depart_t || 0));
  element('message-ecart').hidden = true;
  afficherCompteur(location.balise, duree, prixEstime(duree), true);
}

/* Le vrai reçu (et le rapport d'état) arrive quand le cloud répond. */
function afficherRecuLocal(location) {
  const duree = Math.max(0, (location.retour_t || 0) - (location.depart_t || 0));
  vue.sessionRecu = null;
  element('recu-planche').textContent = numeroPlanche(location.balise);
  element('recu-duree').textContent = formaterDuree(duree);
  element('recu-caution').textContent = 'libération au retour du réseau';
  element('recu-debit').textContent =
    `≈ ${formaterEuros(prixEstime(duree))} · débit au retour du réseau`;
  afficherLiens('recu-liens', []);
  element('formulaire-etat').hidden = true;
  element('merci-etat').hidden = true;
  afficherEcran('recu');
}

/* ---------- Navigation et disponibilités ---------- */

function afficherEcran(id) {
  if (element(id).classList.contains('active')) return;
  for (const ecran of document.querySelectorAll('.screen')) {
    ecran.classList.toggle('active', ecran.id === id);
  }
  suivreParc(id === 'pret');
  actualiserBandeau();
  window.scrollTo(0, 0);
}

function naviguer(destination) {
  arreterSondage();
  fermerSession(vue.sessionAffichee);
  vue.sessionAffichee = null;
  oublierLocationTerminee();
  element('suivi').hidden = true;
  element('annonce').hidden = true;
  if (destination === 'identification') preparerIdentification();
  afficherEcran(destination);
}

function afficherAnnonce(texte) {
  const annonce = element('annonce');
  annonce.replaceChildren(...texteEnrichi(texte));
  annonce.hidden = !texte;
}

/* Tant que l'écran « prêt » est affiché, les disponibilités restent à jour. */
function suivreParc(actif) {
  clearInterval(vue.parc);
  vue.parc = actif ? setInterval(chargerParc, INTERVALLE_SONDAGE) : null;
  if (actif) chargerParc();
}

/* Cloud muet : les disponibilités viennent de la station. */
async function chargerParc() {
  if (vue.parcEnAttente) return;
  vue.parcEnAttente = true;
  try {
    afficherParc(await appelerApi('/api/parc'));
  } catch (erreur) {
    if (erreur.reseau) await chargerParcLocal();
    else if (!erreur.ignoree) element('nombre-planches').textContent = '—';
  } finally {
    vue.parcEnAttente = false;
  }
}

function afficherParc({planches, ouvert}) {
  const libres = Object.values(planches || {}).filter(
    planche => planche.ou === STATION && planche.statut === 'au râtelier');
  const possible = Boolean(ouvert) && libres.length > 0;
  afficherNombrePlanches(libres.length);
  element('bouton-surfer').disabled = !possible;
  afficherHoraires(possible, texteHoraires(ouvert, libres.length > 0));
}

function afficherNombrePlanches(libres) {
  const inconnu = libres === null;
  element('nombre-planches').textContent = inconnu ? '—' : libres;
  element('texte-planches').textContent =
    inconnu || libres > 1 ? 'planches disponibles' : 'planche disponible';
}

function afficherHoraires(possible, texte) {
  const horaires = element('horaires');
  horaires.classList.toggle('ferme', !possible);
  horaires.textContent = texte;
}

function texteHoraires(ouvert, disponible) {
  if (!ouvert) return 'Fermé : plus de location à partir de 22 h.';
  if (!disponible) return 'Plus de planche disponible pour le moment.';
  return 'Locations jusqu’à 22 h · 0,20 € / minute';
}

/* ---------- Identification et moyen de paiement ---------- */

function preparerIdentification() {
  const client = clientMemorise();
  if (client) {
    element('prenom').value = client.prenom || '';
    element('nom').value = client.nom || '';
    element('telephone').value = client.telephone || '';
  }
  vue.moyenManuel = false;
  element('nouveau-portefeuille').checked = false;
  effacerErreur();
  proposerMoyenEnregistre(client && client.paiement);
}

function proposerMoyenEnregistre(paiement) {
  const disponible = Boolean(paiement && paiement.libelle);
  element('moyen-enregistre').hidden = !disponible;
  element('changer-moyen').hidden = false;
  element('choix-moyens').hidden = disponible;
  if (disponible) {
    element('libelle-enregistre').textContent = paiement.libelle;
    element('icone-enregistre').setAttribute(
      'href', ICONES[paiement.type] || ICONES.carte);
  }
  choisirMoyen(disponible ? 'enregistre' : 'carte');
}

/* Le cloud n'accepte plus le moyen enregistré : on ne le propose plus. */
function oublierMoyenEnregistre() {
  oublierPaiementMemorise();
  element('moyen-enregistre').hidden = true;
  afficherChoixMoyens();
}

function afficherChoixMoyens() {
  element('choix-moyens').hidden = false;
  element('changer-moyen').hidden = true;
  choisirMoyen('carte');
}

function surChangerMoyen() {
  vue.moyenManuel = true;
  afficherChoixMoyens();
}

function surMoyenChoisi() {
  vue.moyenManuel = true;
  mettreAJourMoyen();
}

/*
 * Le moyen enregistré n'est proposé que pour le numéro mémorisé ; il
 * revient si l'on retrouve ce numéro, sans écraser un choix fait à la main.
 */
function surTelephoneModifie() {
  const client = clientMemorise();
  const saisi = telephoneNormalise(element('telephone').value);
  const connu = Boolean(client && client.paiement)
    && telephoneNormalise(client.telephone) === saisi;
  if (connu && !vue.moyenManuel) {
    return proposerMoyenEnregistre(client.paiement);
  }
  element('moyen-enregistre').hidden = !connu;
  if (!connu && moyenChoisi() === 'enregistre') afficherChoixMoyens();
}

function choisirMoyen(moyen) {
  const selecteur = `input[name="moyen"][value="${moyen}"]`;
  document.querySelector(selecteur).checked = true;
  mettreAJourMoyen();
}

function moyenChoisi() {
  return document.querySelector('input[name="moyen"]:checked').value;
}

function mettreAJourMoyen() {
  const moyen = moyenChoisi();
  element('champs-carte').hidden = moyen !== 'carte';
  element('note-crypto').hidden = moyen !== 'crypto';
  proposerNouveauPortefeuille(moyen === 'crypto');
}

/* Un client qui paie déjà en crypto peut changer de portefeuille. */
function proposerNouveauPortefeuille(cryptoChoisie) {
  const client = clientMemorise();
  const paiement = client && client.paiement;
  const connu = Boolean(paiement && paiement.type === 'crypto');
  element('choix-nouveau-portefeuille').hidden = !(cryptoChoisie && connu);
  if (connu) {
    element('texte-nouveau-portefeuille').textContent =
      `Utiliser un nouveau portefeuille (au lieu de ${paiement.libelle})`;
  }
}

/* ---------- Vérifications (carte comprise, comme Stripe.js) ---------- */

function estRempli(texte) {
  return texte.trim() !== '';
}

/* Même règle que le serveur : « 06 12 34 56 78 » devient « +33612345678 ». */
function telephoneNormalise(texte) {
  const brut = String(texte || '').replace(/[^\d+]/g, '')
    .replace(/^(\+|00)330?/, '0');
  return /^0[1-9]\d{8}$/.test(brut) ? '+33' + brut.slice(1) : brut;
}

function telephoneValide(texte) {
  return /^\+\d{8,15}$/.test(telephoneNormalise(texte));
}

function respecteLuhn(numero) {
  const somme = [...numero].reverse().reduce((total, chiffre, rang) => {
    const valeur = Number(chiffre) * (rang % 2 === 1 ? 2 : 1);
    return total + (valeur > 9 ? valeur - 9 : valeur);
  }, 0);
  return somme % 10 === 0;
}

function numeroCarteValide(texte) {
  const numero = chiffres(texte);
  return numero.length >= 13 && numero.length <= 19 && respecteLuhn(numero);
}

function expirationValide(texte) {
  const morceaux = /^(\d{2})\/(\d{2})$/.exec(texte.trim());
  if (!morceaux) return false;
  const mois = Number(morceaux[1]);
  const finDeValidite = new Date(2000 + Number(morceaux[2]), mois, 1);
  return mois >= 1 && mois <= 12 && finDeValidite > new Date();
}

function cvcValide(texte) {
  return /^\d{3}$/.test(texte.trim());
}

function marqueCarte(numero) {
  if (numero.startsWith('4')) return 'Visa';
  if (/^(5[1-5]|2[2-7])/.test(numero)) return 'Mastercard';
  return 'Carte';
}

function grouperParQuatre(saisie) {
  return chiffres(saisie).slice(0, 19).replace(/(\d{4})(?=\d)/g, '$1 ');
}

function formaterExpiration(saisie) {
  let valeur = chiffres(saisie);
  if (valeur.length === 6) valeur = valeur.slice(0, 2) + valeur.slice(4);
  valeur = valeur.slice(0, 4);
  if (valeur.length <= 2) return valeur;
  return valeur.slice(0, 2) + '/' + valeur.slice(2);
}

function formaterCvc(saisie) {
  return chiffres(saisie).slice(0, 3);
}

function reformaterALaSaisie(id, miseEnForme) {
  const champ = element(id);
  champ.addEventListener('input', () => {
    champ.value = miseEnForme(champ.value);
  });
}

function remplirCarteDeTest() {
  for (const [id, valeur] of Object.entries(CARTE_DE_TEST)) {
    element(id).value = valeur;
  }
  effacerErreur();
}

function viderChampsCarte() {
  for (const id of Object.keys(CARTE_DE_TEST)) element(id).value = '';
}

/* ---------- Réservation ---------- */

function premiereErreur(regles) {
  const regle = regles.find(([id, valide]) => !valide(element(id).value));
  return regle && {champ: regle[0], message: regle[2]};
}

/* Erreur de saisie sous le champ fautif ; sinon, juste au-dessus du bouton. */
function signalerErreur({champ, message}) {
  const zone = element('erreur-identification');
  if (champ) {
    const fautif = element(champ);
    fautif.after(zone);
    fautif.setAttribute('aria-invalid', 'true');
    fautif.setAttribute('aria-describedby', zone.id);
    fautif.focus({preventScroll: true});
    fautif.scrollIntoView({block: 'center'});
  }
  zone.textContent = message;
}

function effacerErreur() {
  const zone = element('erreur-identification');
  zone.textContent = '';
  element('bouton-reserver').before(zone);
  for (const champ of document.querySelectorAll('[aria-invalid]')) {
    champ.removeAttribute('aria-invalid');
    champ.removeAttribute('aria-describedby');
  }
}

function surReservation(evenement) {
  evenement.preventDefault();
  effacerErreur();
  const moyen = moyenChoisi();
  const regles = moyen === 'carte'
    ? [...REGLES_IDENTITE, ...REGLES_CARTE] : REGLES_IDENTITE;
  const erreur = premiereErreur(regles);
  if (erreur) return signalerErreur(erreur);
  if (moyen === 'apple_pay') return element('feuille-apple-pay').showModal();
  reserver();
}

function confirmerApplePay() {
  element('feuille-apple-pay').close();
  reserver();
}

function moyenAEnvoyer(moyen) {
  if (moyen === 'crypto') {
    return {type: 'crypto', nouveau: element('nouveau-portefeuille').checked};
  }
  if (moyen !== 'carte') return {type: moyen};
  const numero = chiffres(element('numero-carte').value);
  return {
    type: 'carte',
    marque: marqueCarte(numero),
    derniers4: numero.slice(-4),
    expiration: element('expiration').value.trim(),
  };
}

/* « generation » : la dernière connue, pour qu'une démo réinitialisée refuse. */
function corpsReservation() {
  const corps = {
    identifiant: vue.identifiant,
    station: STATION,
    client: {
      prenom: element('prenom').value.trim(),
      nom: element('nom').value.trim(),
      telephone: element('telephone').value.trim(),
    },
    moyen: moyenAEnvoyer(moyenChoisi()),
  };
  if (vue.generation !== null) corps.generation = vue.generation;
  return corps;
}

function basculerAttente(enAttente) {
  element('bouton-reserver').disabled = enAttente;
  element('texte-reserver').textContent =
    enAttente ? 'Réservation…' : 'Réserver ma planche';
}

async function reserver() {
  const corps = corpsReservation();
  basculerAttente(true);
  try {
    apresReservation(corps.client, await appelerApi('/api/arme', corps));
  } catch (erreur) {
    if (!erreur.ignoree) signalerEchecReservation(erreur, corps.moyen.type);
  } finally {
    basculerAttente(false);
  }
}

/*
 * Cloud muet : on garde le moyen enregistré et on oriente vers la station.
 * Si le cloud refuse le moyen enregistré, les autres moyens sont proposés ;
 * un refus d'horaire ou de parc (« Plus de planche… ») le laisse en place.
 */
function signalerEchecReservation(erreur, moyen) {
  if (erreur.reseau) {
    const message = autorisationValide() ? MESSAGE_RETOUR_STATION : MESSAGE_RESEAU;
    return signalerErreur({message});
  }
  const refusDuMoyen = REFUS_DU_MOYEN.test(erreur.message);
  if (moyen === 'enregistre' && refusDuMoyen) oublierMoyenEnregistre();
  signalerErreur({message: erreur.message});
}

/* Le numéro est gardé tel que saisi ; jamais aucune donnée de carte. */
function memoriserClient(client, paiement) {
  const fiche = paiement
    ? {...client, paiement: {type: paiement.type, libelle: paiement.libelle}}
    : client;
  ecrireMemoire(CLE_CLIENT, JSON.stringify(fiche));
}

function apresReservation(client, donnees) {
  memoriserClient(client, donnees.paiement);
  if (donnees.offline_authorization) {
    memoriserAutorisation(donnees.offline_authorization, donnees.session_id);
  }
  oublierReservationLocale();
  viderChampsCarte();
  element('erreur-annulation').textContent = '';
  afficherClient(donnees);
  demarrerSondage();
}

async function annulerReservation() {
  const bouton = element('annuler-reservation');
  const corps = {identifiant: vue.identifiant};
  bouton.disabled = true;
  element('erreur-annulation').textContent = '';
  try {
    afficherClient(await appelerApi('/api/annuler', corps));
  } catch (erreur) {
    if (!erreur.ignoree) element('erreur-annulation').textContent = erreur.message;
  } finally {
    bouton.disabled = false;
  }
}

/* ---------- Suivi de la location ---------- */

function afficherClient(donnees) {
  retenirSessionEnLigne(donnees);
  if (sessionFermee(donnees)) return;
  if (donnees.etat === 'annulée') return afficherAnnulation(donnees);
  const afficher = AFFICHAGES[donnees.etat];
  if (!afficher) return;
  afficher(donnees);
  afficherSuivi(donnees);
  const close = ETATS_CLOS.includes(donnees.etat);
  vue.sessionAffichee = close ? donnees.session_id : null;
}

/* Retour sur « Je veux surfer », avec le message d'annulation du cloud. */
function afficherAnnulation({messages = [], session_id: session}) {
  naviguer('pret');
  fermerSession(session);
  const dernier = messages[messages.length - 1];
  afficherAnnonce(dernier && dernier.texte);
}

function afficherSuivi(donnees) {
  const enLocation = donnees.etat === 'armée' || donnees.etat === 'en cours';
  element('suivi').hidden = false;
  element('garantie').hidden = !enLocation;
  element('caution-texte').textContent = texteCaution(donnees);
  afficherLiens('caution-liens', (donnees.caution || {}).liens);
  afficherMessages(donnees.messages);
}

/* Une location reprise d'une station hors ligne peut n'avoir ni caution ni moyen. */
function texteCaution({caution, paiement}) {
  if (!caution) return '';
  const {type, libelle} = paiement || {};
  const montant = 'Caution de ' + formaterEuros(caution.montant, 0);
  const lieu = type === 'crypto' ? 'sur Fuji' : 'en cours';
  const textes = {
    'en attente': `${montant} : blocage ${lieu}…`,
    'bloquée': `${montant} bloquée ✓` + (libelle ? '\n' + libelle : ''),
    'libérée': `${montant} libérée ✓`,
    'débitée': `${montant} débitée`,
    'échec': `${montant} : le blocage a échoué, contactez KORKO.`,
  };
  return textes[caution.etat] || montant;
}

function remplirListe(conteneur, elements, creer) {
  const signature = JSON.stringify(elements);
  if (conteneur.dataset.signature === signature) return;
  conteneur.dataset.signature = signature;
  conteneur.replaceChildren(...elements.map(creer));
}

function afficherLiens(id, liens) {
  const surs = (liens || []).filter(lien => String(lien).startsWith('https://'));
  remplirListe(element(id), surs, creerLienSnowtrace);
}

function creerLienSnowtrace(url, rang) {
  const lien = document.createElement('a');
  lien.href = url;
  lien.target = '_blank';
  lien.rel = 'noopener';
  lien.textContent = `Preuve ${rang + 1} sur Snowtrace ↗`;
  return lien;
}

function afficherMessages(messages) {
  const liste = messages || [];
  element('messages').hidden = liste.length === 0;
  const recentsDabord = [...liste].reverse();
  remplirListe(element('messages-liste'), recentsDabord, creerMessage);
}

function creerMessage({heure, texte}) {
  const ligne = document.createElement('li');
  const moment = document.createElement('time');
  moment.textContent = heure;
  ligne.append(moment, ...texteEnrichi(texte));
  return ligne;
}

function afficherReservee(donnees) {
  element('planche-reservee').textContent = numeroPlanche(donnees.balise);
  proposerAnnulation(!donnees.offline);
  afficherEcran('reservee');
}

function afficherEnCours(donnees) {
  const ecart = element('message-ecart');
  ecart.textContent = donnees.message || '';
  ecart.hidden = !donnees.message;
  afficherCompteur(donnees.balise, donnees.duree, donnees.montant, false);
}

function afficherCompteur(balise, duree, montant, estimation) {
  element('planche-en-cours').textContent = numeroPlanche(balise);
  element('duree').textContent = formaterDuree(duree);
  element('prix-courant').textContent = formaterEuros(montant);
  element('note-estimation').hidden = !estimation;
  afficherEcran('en-cours');
}

function afficherCautionDebitee({caution, paiement}) {
  const {montant: montantCaution, liens} = caution || {};
  const montant = formaterEuros(montantCaution, 0);
  const sur = paiement ? ` sur ${paiement.libelle}` : '';
  element('debitee-detail').textContent = `Caution de ${montant} débitée${sur}.`;
  afficherLiens('debitee-liens', liens);
  afficherEcran('caution-debitee');
}

/* ---------- Reçu et état de la planche ---------- */

function texteDebit({debite, montant, paiement}) {
  if (debite == null) return formaterEuros(montant) + ' · débit en cours…';
  const sur = paiement ? ' sur ' + paiement.libelle : '';
  return formaterEuros(debite) + sur;
}

function texteCautionRecu(caution) {
  if (!caution) return '—';
  const enCours = caution.etat === 'bloquée' || caution.etat === 'en attente';
  return enCours ? 'libération en cours…' : caution.etat;
}

function afficherRecu(donnees) {
  if (vue.sessionRecu !== donnees.session_id) {
    preparerRapport(donnees.session_id);
  }
  element('recu-planche').textContent = numeroPlanche(donnees.balise);
  element('recu-duree').textContent = formaterDuree(donnees.duree);
  element('recu-caution').textContent = texteCautionRecu(donnees.caution);
  element('recu-debit').textContent = texteDebit(donnees);
  afficherLiens('recu-liens', (donnees.caution || {}).liens);
  const envoye = Boolean(donnees.rapport_condition);
  const passe = vue.sessionPassee === donnees.session_id;
  element('formulaire-etat').hidden = envoye || passe;
  element('merci-etat').hidden = !envoye;
  afficherEcran('recu');
}

function boutonsCondition() {
  return document.querySelectorAll('[data-condition]');
}

function marquerChoix(bouton, choisi) {
  bouton.classList.toggle('selected', choisi);
  bouton.setAttribute('aria-checked', String(choisi));
}

function preparerRapport(sessionId) {
  vue.sessionRecu = sessionId;
  vue.conditionChoisie = '';
  basculerEnvoiRapport(false);
  element('valider-etat').disabled = true;
  element('erreur-etat').textContent = '';
  element('photo').value = '';
  surPhotoChoisie();
  for (const bouton of boutonsCondition()) marquerChoix(bouton, false);
}

function choisirCondition(bouton) {
  if (vue.envoiCondition) return;
  vue.conditionChoisie = bouton.dataset.condition;
  for (const autre of boutonsCondition()) marquerChoix(autre, autre === bouton);
  element('valider-etat').disabled = false;
  element('erreur-etat').textContent = '';
}

function surPhotoChoisie() {
  const photo = element('photo').files[0];
  element('note-photo').textContent = photo
    ? `Photo choisie : ${photo.name} (prototype, non envoyée).`
    : 'Prototype : la photo ne sera pas envoyée.';
}

function basculerEnvoiRapport(enCours) {
  vue.envoiCondition = enCours;
  element('valider-etat').disabled = enCours;
  element('valider-etat').textContent = enCours ? 'Envoi…' : 'Valider l’état';
  element('passer-etat').disabled = enCours;
}

function corpsRapport() {
  const photo = element('photo').files[0];
  return {
    identifiant: vue.identifiant,
    session_id: vue.sessionRecu,
    condition: vue.conditionChoisie,
    photo: photo ? photo.name : null,
  };
}

async function envoyerRapport() {
  if (!vue.conditionChoisie || vue.envoiCondition) return;
  basculerEnvoiRapport(true);
  try {
    await appelerApi('/api/condition', corpsRapport());
    element('formulaire-etat').hidden = true;
    element('merci-etat').hidden = false;
  } catch (erreur) {
    basculerEnvoiRapport(false);
    if (erreur.ignoree) return;
    element('erreur-etat').textContent =
      'Envoi impossible. Vous pouvez réessayer ou passer.';
  }
}

function passerRapport() {
  vue.sessionPassee = vue.sessionRecu;
  element('formulaire-etat').hidden = true;
}

/* ---------- Démarrage ---------- */

function brancherNavigation() {
  for (const bouton of document.querySelectorAll('[data-go]')) {
    bouton.addEventListener('click', () => naviguer(bouton.dataset.go));
  }
  element('annuler-reservation').addEventListener('click', annulerReservation);
  element('bouton-hors-ligne').addEventListener('click', prendrePlancheHorsLigne);
  document.querySelector('.brand').addEventListener('click', surLogo);
}

/* Hors ligne, recharger la page (servie par le cloud) mènerait à une page d'erreur. */
function surLogo(evenement) {
  if (vue.horsLigne) evenement.preventDefault();
}

function brancherIdentification() {
  const formulaire = element('formulaire-identification');
  formulaire.addEventListener('submit', surReservation);
  formulaire.addEventListener('input', effacerErreur);
  for (const radio of document.querySelectorAll('input[name="moyen"]')) {
    radio.addEventListener('change', surMoyenChoisi);
  }
  element('telephone').addEventListener('input', surTelephoneModifie);
  element('changer-moyen').addEventListener('click', surChangerMoyen);
  element('carte-de-test').addEventListener('click', remplirCarteDeTest);
  reformaterALaSaisie('numero-carte', grouperParQuatre);
  reformaterALaSaisie('expiration', formaterExpiration);
  reformaterALaSaisie('cvc', formaterCvc);
}

function brancherApplePay() {
  const feuille = element('feuille-apple-pay');
  element('confirmer-apple-pay').addEventListener('click', confirmerApplePay);
  element('annuler-apple-pay').addEventListener('click', () => feuille.close());
}

function brancherRapport() {
  for (const bouton of boutonsCondition()) {
    bouton.addEventListener('click', () => choisirCondition(bouton));
  }
  const photo = element('photo');
  element('bouton-photo').addEventListener('click', () => photo.click());
  photo.addEventListener('change', surPhotoChoisie);
  element('valider-etat').addEventListener('click', envoyerRapport);
  element('passer-etat').addEventListener('click', passerRapport);
}

brancherNavigation();
brancherIdentification();
brancherApplePay();
brancherRapport();
suivreParc(true);
demarrerSondage();
