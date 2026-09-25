/*
 * KORKO : parcours du client sur son téléphone.
 * accueil -> prêt -> identification et paiement -> planche réservée
 * -> session en cours -> reçu (ou caution débitée après 23 h).
 * Le serveur ne reçoit jamais le numéro complet de la carte ni son CVC.
 */

const STATION = 'A';
const INTERVALLE_SONDAGE = 1200;
const CLE_APPAREIL = 'korko-session';
const CLE_CLIENT = 'korko-client';
const MESSAGE_RESEAU = 'Connexion impossible. Vérifiez le réseau et réessayez.';
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
const AFFICHAGES = {
  'armée': afficherReservee,
  'en cours': afficherEnCours,
  'retournée': afficherRecu,
  'caution débitée': afficherCautionDebitee,
};
const vue = {
  sondage: null,
  sessionRecu: null,
  sessionPassee: null,
  conditionChoisie: '',
  envoiCondition: false,
};
const IDENTIFIANT = identifiantAppareil();

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

function clientMemorise() {
  try {
    return JSON.parse(lireMemoire(CLE_CLIENT));
  } catch {
    return null;
  }
}

function identifiantAppareil() {
  const connu = lireMemoire(CLE_APPAREIL);
  if (connu) return connu;
  const octets = crypto.getRandomValues(new Uint8Array(16));
  const nouveau = Array.from(octets, o => o.toString(16).padStart(2, '0'));
  ecrireMemoire(CLE_APPAREIL, nouveau.join(''));
  return nouveau.join('');
}

/* ---------- Échanges avec le cloud ---------- */

function optionsRequete(corps) {
  if (!corps) return {};
  return {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(corps),
  };
}

async function appelerApi(url, corps) {
  let reponse;
  try {
    reponse = await fetch(url, optionsRequete(corps));
  } catch {
    throw new Error(MESSAGE_RESEAU);
  }
  const donnees = await reponse.json().catch(() => ({}));
  if (!reponse.ok) throw new Error(donnees.erreur || MESSAGE_RESEAU);
  return donnees;
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

async function rafraichir() {
  const url = '/api/client?identifiant=' + encodeURIComponent(IDENTIFIANT);
  let donnees;
  try {
    donnees = await appelerApi(url);
  } catch {
    return;
  }
  if (vue.sondage) afficherClient(donnees);
}

/* ---------- Navigation ---------- */

function afficherEcran(id) {
  if (element(id).classList.contains('active')) return;
  for (const ecran of document.querySelectorAll('.screen')) {
    ecran.classList.toggle('active', ecran.id === id);
  }
  window.scrollTo(0, 0);
}

function naviguer(destination) {
  arreterSondage();
  element('suivi').hidden = true;
  afficherEcran(destination);
  if (destination === 'pret') chargerParc();
}

async function chargerParc() {
  try {
    afficherParc(await appelerApi('/api/parc'));
  } catch {
    element('nombre-planches').textContent = '—';
  }
}

function afficherParc({planches, ouvert, heure}) {
  const libres = Object.values(planches).filter(
    planche => planche.ou === STATION && planche.statut === 'au râtelier');
  element('nombre-planches').textContent = libres.length;
  element('bouton-surfer').disabled = !ouvert;
  const horaires = element('horaires');
  horaires.classList.toggle('ferme', !ouvert);
  horaires.textContent = ouvert
    ? `Locations jusqu’à 22 h · il est ${heure}`
    : `Fermé : plus de location après 22 h (il est ${heure}).`;
}

/* ---------- Identification et moyen de paiement ---------- */

function preparerIdentification() {
  const client = clientMemorise();
  if (client) {
    element('prenom').value = client.prenom || '';
    element('nom').value = client.nom || '';
    element('telephone').value = client.telephone || '';
  }
  proposerMoyenEnregistre(client && client.paiement);
}

function proposerMoyenEnregistre(paiement) {
  const disponible = Boolean(paiement && paiement.libelle);
  element('moyen-enregistre').hidden = !disponible;
  element('changer-moyen').hidden = false;
  element('choix-moyens').hidden = disponible;
  if (disponible) element('libelle-enregistre').textContent = paiement.libelle;
  choisirMoyen(disponible ? 'enregistre' : 'carte');
}

function afficherChoixMoyens() {
  element('choix-moyens').hidden = false;
  element('changer-moyen').hidden = true;
  choisirMoyen('carte');
}

function surTelephoneModifie() {
  const client = clientMemorise();
  const saisi = chiffres(element('telephone').value);
  const connu = client && chiffres(client.telephone) === saisi;
  if (connu || element('moyen-enregistre').hidden) return;
  element('moyen-enregistre').hidden = true;
  afficherChoixMoyens();
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
}

/* ---------- Vérifications (carte comprise, comme Stripe.js) ---------- */

function estRempli(texte) {
  return texte.trim() !== '';
}

function telephoneValide(texte) {
  return chiffres(texte).length >= 8;
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

function signalerErreur({champ, message}) {
  const zone = element('erreur-identification');
  zone.textContent = message;
  if (!champ) return;
  element(champ).setAttribute('aria-invalid', 'true');
  element(champ).after(zone);
  element(champ).focus();
}

function effacerErreur() {
  const zone = element('erreur-identification');
  zone.textContent = '';
  element('bouton-reserver').before(zone);
  for (const champ of document.querySelectorAll('[aria-invalid]')) {
    champ.removeAttribute('aria-invalid');
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
  if (moyen !== 'carte') return {type: moyen};
  const numero = chiffres(element('numero-carte').value);
  return {
    type: 'carte',
    marque: marqueCarte(numero),
    derniers4: numero.slice(-4),
    expiration: element('expiration').value.trim(),
  };
}

function corpsReservation() {
  return {
    identifiant: IDENTIFIANT,
    station: STATION,
    client: {
      prenom: element('prenom').value.trim(),
      nom: element('nom').value.trim(),
      telephone: element('telephone').value.trim(),
    },
    moyen: moyenAEnvoyer(moyenChoisi()),
  };
}

function basculerAttente(enAttente) {
  element('bouton-reserver').disabled = enAttente;
  element('texte-reserver').textContent =
    enAttente ? 'Réservation…' : 'Réserver ma planche';
}

async function reserver() {
  basculerAttente(true);
  try {
    apresReservation(await appelerApi('/api/arme', corpsReservation()));
  } catch (erreur) {
    signalerErreur({message: erreur.message});
  } finally {
    basculerAttente(false);
  }
}

function memoriserClient({client, paiement}) {
  const fiche = {
    prenom: client.prenom,
    nom: client.nom,
    telephone: client.telephone,
    paiement: {type: paiement.type, libelle: paiement.libelle},
  };
  ecrireMemoire(CLE_CLIENT, JSON.stringify(fiche));
}

function apresReservation(donnees) {
  memoriserClient(donnees);
  viderChampsCarte();
  proposerMoyenEnregistre(donnees.paiement);
  afficherClient(donnees);
  demarrerSondage();
}

/* ---------- Suivi de la location ---------- */

function afficherClient(donnees) {
  const afficher = AFFICHAGES[donnees.etat];
  if (!afficher) return;
  afficher(donnees);
  afficherSuivi(donnees);
}

function afficherSuivi(donnees) {
  const enLocation = donnees.etat === 'armée' || donnees.etat === 'en cours';
  element('suivi').hidden = false;
  element('garantie').hidden = !enLocation;
  element('caution-texte').textContent = texteCaution(donnees);
  afficherLiens('caution-liens', donnees.caution.liens);
  afficherMessages(donnees.messages);
}

function texteCaution({caution, paiement}) {
  const montant = 'Caution de ' + formaterEuros(caution.montant, 0);
  const lieu = paiement.type === 'crypto' ? 'sur Fuji' : 'en cours';
  const textes = {
    'en attente': `${montant} : blocage ${lieu}…`,
    'bloquée': `${montant} bloquée ✓\n${paiement.libelle}`,
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

function afficherLiens(id, liens = []) {
  const surs = liens.filter(lien => String(lien).startsWith('https://'));
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

function afficherMessages(messages = []) {
  element('messages').hidden = messages.length === 0;
  const recentsDabord = [...messages].reverse();
  remplirListe(element('messages-liste'), recentsDabord, creerMessage);
}

function creerMessage({heure, texte}) {
  const ligne = document.createElement('li');
  const moment = document.createElement('time');
  moment.textContent = heure;
  ligne.append(moment, texte);
  return ligne;
}

function afficherReservee(donnees) {
  element('planche-reservee').textContent = numeroPlanche(donnees.balise);
  afficherEcran('reservee');
}

function afficherEnCours(donnees) {
  const ecart = element('message-ecart');
  ecart.textContent = donnees.message || '';
  ecart.hidden = !donnees.message;
  element('planche-en-cours').textContent = numeroPlanche(donnees.balise);
  element('duree').textContent = formaterDuree(donnees.duree);
  element('prix-courant').textContent = formaterEuros(donnees.montant);
  afficherEcran('en-cours');
}

function afficherCautionDebitee({caution, paiement}) {
  const montant = formaterEuros(caution.montant, 0);
  element('debitee-detail').textContent =
    `Caution de ${montant} débitée sur ${paiement.libelle}.`;
  afficherLiens('debitee-liens', caution.liens);
  afficherEcran('caution-debitee');
}

/* ---------- Reçu et état de la planche ---------- */

function texteDebit({debite, montant, paiement}) {
  if (debite == null) return formaterEuros(montant) + ' · débit en cours…';
  return formaterEuros(debite) + ' sur ' + paiement.libelle;
}

function texteCautionRecu(caution) {
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
  afficherLiens('recu-liens', donnees.caution.liens);
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
    identifiant: IDENTIFIANT,
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
  } catch {
    basculerEnvoiRapport(false);
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
}

function brancherIdentification() {
  const formulaire = element('formulaire-identification');
  formulaire.addEventListener('submit', surReservation);
  formulaire.addEventListener('input', effacerErreur);
  for (const radio of document.querySelectorAll('input[name="moyen"]')) {
    radio.addEventListener('change', mettreAJourMoyen);
  }
  element('telephone').addEventListener('input', surTelephoneModifie);
  element('changer-moyen').addEventListener('click', afficherChoixMoyens);
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
preparerIdentification();
demarrerSondage();
