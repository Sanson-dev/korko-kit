(() => {
  const $ = id => document.getElementById(id);
  const nouveauId = () => crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
  const board = value => (value || '').replace('korko-', '');
  const money = value => Number(value || 0).toLocaleString('fr-FR', {
    minimumFractionDigits: 2, maximumFractionDigits: 2
  }) + ' €';
  const duration = value => {
    const secondes = Math.max(0, Math.floor(value || 0));
    return String(Math.floor(secondes / 60)).padStart(2, '0') + ':' +
      String(secondes % 60).padStart(2, '0');
  };
  const show = id => {
    document.querySelectorAll('.screen').forEach(screen => screen.classList.remove('active'));
    $(id).classList.add('active');
  };

  let id = localStorage.getItem('korko-session') || nouveauId();
  let phone = localStorage.getItem('korko-phone') || '';
  let poller, receiptSession = null, hadSession = false, generation = null;
  let resetDetected = false, armSubmitting = false, stateRequest = 0;
  let photoSubmitting = false, retryingPhotoSession = null, photoPreviewUrl = null;
  let photoTaskId = null, photoFailure = false;
  localStorage.setItem('korko-session', id);
  $('phone-input').value = phone;

  async function park() {
    const response = await fetch('/api/parc');
    const data = await response.json();
    $('board-count').textContent = Object.values(data.planches)
      .filter(planche => planche.ou === 'A' && planche.statut === 'au râtelier').length;
  }

  function clearPhoto(keepInput = false) {
    if (photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl);
    photoPreviewUrl = null;
    if (!keepInput) $('photo-input').value = '';
    $('photo-preview').removeAttribute('src');
    $('photo-preview').hidden = true;
    $('photo-note').textContent = 'JPEG, PNG ou WebP · 8 Mo maximum';
    $('photo-error').textContent = '';
    $('submit-photo').disabled = true;
  }

  function resetCustomer() {
    clearInterval(poller);
    stateRequest++;
    localStorage.removeItem('korko-session');
    localStorage.removeItem('korko-phone');
    sessionStorage.removeItem('korko-reset-message');
    id = nouveauId();
    localStorage.setItem('korko-session', id);
    phone = '';
    $('phone-input').value = '';
    hadSession = false;
    receiptSession = null;
    retryingPhotoSession = null;
    photoTaskId = null;
    photoFailure = false;
    photoSubmitting = false;
    armSubmitting = false;
    resetDetected = true;
    clearPhoto();
    $('arm-button').disabled = false;
    $('photo-button').disabled = false;
    $('skip-photo').disabled = false;
    $('photo-progress').hidden = true;
    $('reset-notice').hidden = false;
    show('landing');
  }

  function renderPhotoResult(result) {
    if (!result || !['conforme', 'autre_planche', 'abimee', 'reprendre'].includes(result.statut)) return;
    const icons = { conforme: '✓', autre_planche: '!', abimee: '!', reprendre: '↻' };
    $('photo-result').className = 'screen centered result-' + result.statut;
    $('photo-result-icon').textContent = icons[result.statut];
    $('photo-result-title').textContent = result.titre;
    $('photo-result-message').textContent = result.message;
    show('photo-result');
  }

  function renderPhotoTask(task) {
    if (!task) return false;
    if (task.status === 'pending' || task.status === 'processing') {
      $('photo-pending-message').textContent = task.status === 'processing'
        ? 'La photo est reçue. Son analyse est en cours.'
        : 'La photo est reçue. Analyse en attente ; le cloud réessaiera si le service est indisponible.';
      show('photo-pending');
      return true;
    }
    if (task.status === 'done' && task.result) {
      photoFailure = false;
      renderPhotoResult(task.result);
      return true;
    }
    if (task.status === 'failed') {
      photoFailure = true;
      $('photo-result').className = 'screen centered result-reprendre';
      $('photo-result-icon').textContent = '!';
      $('photo-result-title').textContent = 'Analyse interrompue';
      $('photo-result-message').textContent = task.error || 'L’analyse a été arrêtée. Choisissez une photo et réessayez.';
      $('retry-photo').textContent = $('photo-input').files[0]
        ? 'Réessayer avec cette photo' : 'Choisir une photo et réessayer';
      show('photo-result');
      return true;
    }
    return false;
  }

  function render(data) {
    if (generation === null) generation = data.generation;
    if (data.generation !== undefined && data.generation !== generation) {
      generation = data.generation;
      resetCustomer();
      return;
    }
    if (data.etat === 'armée') {
      $('board-name').textContent = board(data.balise);
      hadSession = true;
      show('armed');
    } else if (data.etat === 'expirée') {
      hadSession = true;
      show('expired');
    } else if (data.etat === 'en cours') {
      $('active-board').textContent = board(data.balise);
      $('board-update').textContent = data.message || '';
      $('board-update').hidden = !data.message;
      $('duration').textContent = duration(data.duree);
      $('current-price').textContent = money(data.montant);
      hadSession = true;
      show('active');
    } else if (data.etat === 'retournée') {
      $('receipt-board').textContent = board(data.balise);
      $('final-duration').textContent = duration(data.duree);
      $('final-price').textContent = money(data.montant);
      hadSession = true;
      if (receiptSession !== data.session_id) {
        receiptSession = data.session_id;
        retryingPhotoSession = null;
        photoTaskId = null;
        photoFailure = false;
        clearPhoto();
      }
      if (retryingPhotoSession !== receiptSession && renderPhotoTask(data.photo_task)) {
        return;
      } else {
        show('receipt');
      }
    } else if (data.etat === 'inconnue' && hadSession) {
      resetCustomer();
    }
  }

  async function state() {
    const request = ++stateRequest, requestedId = id;
    try {
      const response = await fetch('/api/client?identifiant=' + encodeURIComponent(requestedId));
      const data = await response.json();
      if (request === stateRequest && requestedId === id) render(data);
    } catch (_) { /* La prochaine lecture réessaiera. */ }
  }

  function polling() {
    clearInterval(poller);
    state();
    poller = setInterval(state, 1200);
  }

  document.querySelectorAll('[data-go]').forEach(button => button.addEventListener('click', async () => {
    if (button.dataset.go === 'ready') {
      try { await park(); } catch (_) { /* Le bouton de réservation signalera l'erreur. */ }
      clearInterval(poller);
      stateRequest++;
    }
    show(button.dataset.go);
  }));

  $('phone-form').addEventListener('submit', async event => {
    event.preventDefault();
    const raw = $('phone-input').value.replace(/[^0-9+]/g, '');
    if (raw.replace(/\D/g, '').length < 8) {
      $('phone-error').textContent = 'Entrez un numéro valide.';
      return;
    }
    resetDetected = false;
    phone = raw;
    localStorage.setItem('korko-phone', phone);
    try {
      await park();
      show('ready');
    } catch (_) {
      $('phone-error').textContent = 'Le serveur est indisponible. Réessayez.';
    }
  });

  $('arm-button').addEventListener('click', async () => {
    if (resetDetected || armSubmitting) return;
    armSubmitting = true;
    $('arm-button').disabled = true;
    $('arm-error').textContent = '';
    try {
      const response = await fetch('/api/arme', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client: phone, station: 'A', identifiant: id, generation })
      });
      const data = await response.json();
      if (data.generation !== undefined && data.generation !== generation) {
        generation = data.generation;
        resetCustomer();
        return;
      }
      if (!response.ok) throw Error(data.erreur);
      render(data);
      polling();
    } catch (error) {
      $('arm-error').textContent = error.message === 'Plus de planche disponible à la station A.'
        ? 'Aucune planche ne peut être réservée pour le moment.'
        : (error.message || 'Impossible de réserver une planche.');
    } finally {
      armSubmitting = false;
      if (!resetDetected) $('arm-button').disabled = false;
    }
  });

  $('photo-button').addEventListener('click', () => $('photo-input').click());
  $('photo-input').addEventListener('change', () => {
    const file = $('photo-input').files[0];
    clearPhoto(true);
    if (!file) return;
    photoTaskId = nouveauId();
    photoFailure = false;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      $('photo-error').textContent = 'Choisissez une photo JPEG, PNG ou WebP.';
      return;
    }
    if (!file.size || file.size > 8 * 1024 * 1024) {
      $('photo-error').textContent = 'La photo doit faire moins de 8 Mo.';
      return;
    }
    photoPreviewUrl = URL.createObjectURL(file);
    $('photo-preview').src = photoPreviewUrl;
    $('photo-preview').hidden = false;
    $('photo-note').textContent = 'Photo choisie : ' + file.name;
    $('submit-photo').disabled = false;
  });

  function lirePhoto(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(Error('Lecture de la photo impossible. Choisissez une autre image.'));
      reader.readAsDataURL(file);
    });
  }

  async function verifierServeurPhoto() {
    let response;
    try {
      response = await fetch('/api/capabilities', { cache: 'no-store' });
    } catch (_) {
      throw Error('Connexion au cloud impossible. Vérifiez que mon_cloud.py est lancé sur le port 9000.');
    }
    const data = await response.json().catch(() => null);
    if (!response.ok || !data || data.photo_verification !== true || data.version !== 'photo-queue-1') {
      throw Error('Le cloud en cours d’exécution est trop ancien. Arrêtez-le puis relancez mon_cloud.py.');
    }
  }

  $('submit-photo').addEventListener('click', async () => {
    const file = $('photo-input').files[0];
    if (!file || photoSubmitting || !receiptSession) return;
    photoSubmitting = true;
    const requestedId = id, requestedSession = receiptSession;
    $('photo-error').textContent = '';
    $('photo-progress').hidden = false;
    $('submit-photo').disabled = true;
    $('submit-photo').textContent = 'Analyse en cours…';
    $('photo-button').disabled = true;
    $('skip-photo').disabled = true;
    try {
      await verifierServeurPhoto();
      const image = await lirePhoto(file);
      let response;
      try {
        response = await fetch('/api/photo-verification', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ identifiant: requestedId, session_id: requestedSession,
                                 task_id: photoTaskId, generation, image })
        });
      } catch (_) {
        throw Error('Envoi interrompu. Vérifiez que le cloud fonctionne et réessayez.');
      }
      const data = await response.json().catch(() => ({}));
      if (requestedId !== id || requestedSession !== receiptSession) return;
      if (data.generation !== undefined && data.generation !== generation) {
        generation = data.generation;
        resetCustomer();
        return;
      }
      if (!response.ok) throw Error(data.erreur || 'Analyse impossible. Réessayez.');
      if (!data.tache) throw Error('Le cloud n’a pas confirmé la réception de la photo. Réessayez.');
      photoTaskId = data.tache.task_id;
      retryingPhotoSession = null;
      renderPhotoTask(data.tache);
    } catch (error) {
      if (requestedId === id && requestedSession === receiptSession) {
        $('photo-error').textContent = error.message || 'Envoi impossible. Vérifiez votre connexion et réessayez.';
      }
    } finally {
      photoSubmitting = false;
      $('photo-progress').hidden = true;
      $('submit-photo').textContent = 'Analyser la photo';
      $('submit-photo').disabled = !$('photo-input').files[0];
      $('photo-button').disabled = false;
      $('skip-photo').disabled = false;
    }
  });

  $('retry-photo').addEventListener('click', () => {
    retryingPhotoSession = receiptSession;
    if (!photoFailure || !$('photo-input').files[0]) clearPhoto();
    show('receipt');
    if (!$('photo-input').files[0]) $('photo-input').click();
  });

  park().catch(() => {});
  state().then(polling);
})();
