# Ce qu'on a fait ce soir

**1. Nos planches sont certifiées sur la blockchain**
Chaque planche de surf a son identité dans un registre public, sur la
blockchain Avalanche. À chaque fois qu'une planche est prise, part à l'eau,
puis revient à la station, c'est écrit dans la blockchain : c'est certifié, et
personne ne peut le modifier après coup, pas même nous.

**2. Les moyens de paiement sont en place**
Le client paie par carte bancaire, Apple Pay ou crypto. Il ne paie qu'à la
fin de sa session (0,20 € / minute). Une caution de 300 € est bloquée
pendant la location, et débitée seulement si la planche n'est pas rendue
avant 23 h. Une planche réservée doit être prise dans les 10 minutes, sinon
la réservation est annulée sans rien débiter. Le client reçoit un message
quand il part (avec l'heure) et quand il rend la planche.

**3. Un formulaire qui crée la fiche client**
Avant de recevoir sa planche, le client remplit un formulaire : prénom, nom,
téléphone et moyen de paiement. Il est alors enregistré comme client, avec
l'historique de ses locations. La fois suivante, il n'a plus rien à remplir.

**4. Tout marche ensemble, même sans réseau**
Le travail de Sanson et le nôtre sont réunis. Si le cloud est coupé, un
client déjà venu peut quand même prendre une planche : la station s'en
souvient. Quand le cloud revient, tout est rattrapé : la caution, le
paiement et la blockchain.

**5. Une règle à valider ensemble**
À partir de 22 h, on ne peut plus louer de planche. Vous trouvez ça pertinent ?

Pour les détails techniques : `smart_contract/README.md`, `paiement/README.md`
et `OFFLINE_MODE.md`.
