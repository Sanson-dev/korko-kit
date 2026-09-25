(() => {
  let id = localStorage.getItem('korko-session') || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()), poller, receiptSession, hadSession = false, generation = null, resetDetected = false, armSubmitting = false, stateRequest = 0, cloudAvailable = true, lastKnownBoardCount = null;
  let dismissedSessions = new Set();
  const storedDismissed = localStorage.getItem('korko-dismissed-session') || '';
  try {
    const parsedDismissed = JSON.parse(storedDismissed || 'null');
    dismissedSessions = new Set(Array.isArray(parsedDismissed) ? parsedDismissed : parsedDismissed ? [parsedDismissed] : []);
  } catch (_) {
    if (storedDismissed) dismissedSessions.add(storedDismissed);
  }
  const LOCAL_API='http://'+window.location.hostname+':9100';
  localStorage.setItem('korko-session', id);
  let phone = localStorage.getItem('korko-phone') || '';
  let uiFlow = 'landing';
  const $ = x => document.getElementById(x), board = x => (x || '').replace('korko-', '');
  const money = x => Number(x || 0).toLocaleString('fr-FR', {minimumFractionDigits:2, maximumFractionDigits:2}) + ' €';
  const duration = x => { x=Math.max(0,Math.floor(x||0)); return String(Math.floor(x/60)).padStart(2,'0')+':'+String(x%60).padStart(2,'0'); };
  function show(x){uiFlow=x;document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));$(x).classList.add('active')}
  function isDismissedSession(sessionId){return !!sessionId && dismissedSessions.has(String(sessionId))}
  function dismissSession(sessionId){
    if (!sessionId) return;
    dismissedSessions.add(String(sessionId));
    while (dismissedSessions.size > 10) dismissedSessions.delete(dismissedSessions.values().next().value);
    localStorage.setItem('korko-dismissed-session', JSON.stringify([...dismissedSessions]));
  }
  function renderRentalAlert(alert){
    const box = $('rental-alert');
    if (!box) return;
    box.hidden = !alert;
    if (!alert) return;
    box.className = 'rental-alert rental-alert-' + alert.niveau;
    $('rental-alert-title').textContent = alert.titre || '';
    $('rental-alert-message').textContent = alert.message || '';
  }
  function updateBoardAvailability(count){
    const label = $('board-count-label');
    if (count === null || Number.isNaN(Number(count))) {
      if (lastKnownBoardCount !== null) return;
      $('board-count').textContent = '—';
      if (label) label.textContent = 'planches disponibles';
      return;
    }
    lastKnownBoardCount = Number(count);
    $('board-count').textContent = String(lastKnownBoardCount);
    if (label) label.textContent = lastKnownBoardCount <= 1 ? 'planche disponible' : 'planches disponibles';
  }
  async function park(){
    try{
      const d=await (await fetch('/api/parc')).json();
      const count = Object.values(d.planches).filter(p => p.origine === 'A' && p.ou === 'A' && p.statut === 'au râtelier').length;
      updateBoardAvailability(count);
      cloudAvailable=true;
    } catch(_){
      cloudAvailable=false;
      try{
        const r=await fetch(LOCAL_API+'/offline/status');
        const d=await r.json();
        if (d && typeof d.planches_disponibles !== 'undefined') {
          updateBoardAvailability(d.planches_disponibles.length);
        }
      } catch(_){
        if (lastKnownBoardCount === null) {
          $('board-count').textContent = '—';
          const label = $('board-count-label'); if (label) label.textContent = 'planches disponibles';
        }
      }
      updateOfflineControls();
    }
  }
  function validOfflineAuthorization(){try{const a=JSON.parse(localStorage.getItem('korko-offline-authorization')||'null');return a&&a.token&&a.station_id==='A'&&a.expires>Date.now()/1000?a:null}catch(_){return null}}
  function updateOfflineControls(){const eligible=!!validOfflineAuthorization();$('offline-title').hidden=false;$('offline-title').textContent=eligible?'Connexion limitée':'Connexion temporairement indisponible';$('offline-message').hidden=false;$('offline-message').textContent=eligible?'Le service fonctionne en mode hors ligne. Vous pouvez tout de même utiliser une planche.':'Une première connexion est nécessaire avant de pouvoir louer hors ligne.';$('arm-button').textContent=eligible?'Prendre une planche':'Indisponible';$('arm-button').disabled=!eligible}
  let selectedCondition = '', conditionSubmitting = false;
  let photoSubmitting = false, retryingPhotoSession = null, photoPreviewUrl = null;
  let photoTaskId = null, photoFailure = false;
  function clearPhoto(keepInput = false){
    if(photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl);
    photoPreviewUrl=null;
    if(!keepInput)$('photo-input').value='';
    $('photo-preview').removeAttribute('src');
    $('photo-preview').hidden=true;
    $('photo-note').textContent='JPEG, PNG ou WebP · 8 Mo maximum';
    $('photo-error').textContent='';
    $('submit-photo').disabled=true;
  }

  function renderPhotoResult(result){
    if(!result||!['conforme','autre_planche','abimee','reprendre'].includes(result.statut))return false;
    const icons={conforme:'✓',autre_planche:'!',abimee:'!',reprendre:'↻'};
    $('photo-result').className='screen centered result-'+result.statut;
    $('photo-result-icon').textContent=icons[result.statut];
    $('photo-result-title').textContent=result.titre;
    $('photo-result-message').textContent=result.message;
    show('photo-result');
    return true;
  }

  function renderPhotoTask(task){
    if(!task)return false;
    if(task.status==='pending'||task.status==='processing'){
      $('photo-pending-message').textContent=task.status==='processing'
        ? 'La photo est reçue. Son analyse est en cours.'
        : 'La photo est reçue. Analyse en attente ; le cloud réessaiera si le service est indisponible.';
      show('photo-pending');
      return true;
    }
    if(task.status==='done'&&task.result){
      photoFailure=false;
      return renderPhotoResult(task.result);
    }
    if(task.status==='failed'){
      photoFailure=true;
      $('photo-result').className='screen centered result-reprendre';
      $('photo-result-icon').textContent='!';
      $('photo-result-title').textContent='Analyse interrompue';
      $('photo-result-message').textContent=task.error||'L’analyse a été arrêtée. Choisissez une photo et réessayez.';
      $('retry-photo').textContent=$('photo-input').files[0]
        ? 'Réessayer avec cette photo'
        : 'Choisir une photo et réessayer';
      show('photo-result');
      return true;
    }
    return false;
  }

  function resetCustomer(){clearInterval(poller);stateRequest++;localStorage.removeItem('korko-session');localStorage.removeItem('korko-phone');localStorage.removeItem('korko-customer-state');localStorage.removeItem('korko-offline-authorization');localStorage.removeItem('korko-dismissed-session');dismissedSessions.clear();sessionStorage.removeItem('korko-reset-message');id=crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random();localStorage.setItem('korko-session',id);phone='';$('phone-input').value='';hadSession=false;receiptSession=null;conditionSubmitting=false;retryingPhotoSession=null;photoTaskId=null;photoFailure=false;photoSubmitting=false;clearPhoto();resetDetected=true;armSubmitting=false;$('arm-button').disabled=false;$('offline-notice').hidden=true;$('reset-notice').hidden=false;show('landing')}
  function render(d){
    if(!d)return;
    if(generation===null)generation=d.generation;
    if(d.generation!==undefined&&d.generation!==generation){generation=d.generation;resetCustomer();return}
    const terminalState = ['retournée','terminée','retour_tardif','retourne','expirée'].includes(d.etat);
    if (terminalState && isDismissedSession(d.session_id)) {
      return;
    }
    if(d.etat!=='inconnue')localStorage.setItem('korko-customer-state',JSON.stringify({id,phone,state:d}));
    if(d.etat==='armée'){$('board-name').textContent=board(d.balise);hadSession=true;show('armed')}
    if(d.etat==='expirée'){receiptSession=d.session_id;hadSession=true;show('expired')}
    if(d.etat==='en cours'){$('active-board').textContent=board(d.balise);$('board-update').textContent=d.message||'';$('board-update').hidden=!d.message;renderRentalAlert(d.alerte);$('duration').textContent=duration(d.duree);$('current-price').textContent=money(d.montant);hadSession=true;show('active')}
    if(['retournée','terminée','retour_tardif','retourne'].includes(d.etat)){
      renderRentalAlert(null);
      $('receipt-board').textContent=board(d.balise);$('final-duration').textContent=duration(d.duree);$('final-price').textContent=money(d.montant);hadSession=true;
      if(receiptSession!==d.session_id){receiptSession=d.session_id;selectedCondition='';conditionSubmitting=false;retryingPhotoSession=null;photoTaskId=null;photoFailure=false;photoSubmitting=false;clearPhoto();$('condition-error').textContent='';$('submit-condition').textContent="Valider l'état";$('skip-condition').disabled=false;document.querySelectorAll('[data-condition]').forEach(b=>{b.classList.remove('selected');b.setAttribute('aria-checked','false')});$('submit-condition').disabled=true}
      $('condition-form').hidden=!!d.rapport_condition||isDismissedSession(d.session_id);$('condition-thanks').hidden=!d.rapport_condition;
      if(retryingPhotoSession!==receiptSession&&d.photo_task&&renderPhotoTask(d.photo_task))return;
      show('receipt')
    }
    if(d.etat==='inconnue'&&hadSession){return}
  }
  async function state(){
    const request=++stateRequest, requestedId=id;
    try {
      const response=await fetch('/api/client?identifiant='+encodeURIComponent(requestedId));
      if(!response.ok)throw Error('offline');
      const d=await response.json();cloudAvailable=true;
      if(request===stateRequest&&requestedId===id){$('offline-notice').hidden=true;render(d)}
    } catch(_){
      if(request!==stateRequest||requestedId!==id)return;
      cloudAvailable=false;$('offline-notice').hidden=false;updateOfflineControls();
      const auth=validOfflineAuthorization();
      if(auth)try{
        const r=await fetch(LOCAL_API+'/offline/status');
        const local=await r.json(),reservation=local.locations.find(x=>x.authorization_id===id);
        if(reservation){
          const d={generation,balise:reservation.balise,session_id:reservation.offline_reservation_id,
            etat:reservation.status==='TERMINEE'?'retournée':reservation.status==='EN_COURS'?'en cours':'armée',
            depart_a:reservation.depart_t,retour_a:reservation.retour_t,
            duree:Math.max(0,(reservation.retour_t||0)-(reservation.depart_t||0)),
            montant:Math.max(0,(reservation.retour_t||0)-(reservation.depart_t||0))/60*.2};
          if(d.etat==='en cours'){d.duree=Math.max(0,(local.t||reservation.depart_t||0)-reservation.depart_t);d.montant=d.duree/60*.2}
          if(request===stateRequest&&requestedId===id)render(d)
        }
      }catch(_){}
    }
  }
  function polling(){
    clearInterval(poller);
    const tick = async () => {
      await Promise.allSettled([park(), state()]);
    };
    tick();
    poller = setInterval(tick, 1500);
  }
  try{const cached=JSON.parse(localStorage.getItem('korko-customer-state')||'null');if(cached&&cached.id===id&&cached.state){phone=cached.phone||phone;hadSession=true;if (['retournée','terminée','retour_tardif','retourne','expirée'].includes(cached.state.etat)&&isDismissedSession(cached.state.session_id)) { show('landing'); } else { render(cached.state) }}}catch(_){ }
  document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',async()=>{stateRequest++;if(b.dataset.go==='ready'){dismissSession(receiptSession);receiptSession=null;show('ready');await park();}else{show(b.dataset.go)}}));
  $('phone-input').value=phone;
  $('phone-form').addEventListener('submit',async e=>{e.preventDefault();const raw=$('phone-input').value.replace(/[^0-9+]/g,'');if(raw.replace(/\D/g,'').length<8){$('phone-error').textContent='Entrez un numéro valide.';return}stateRequest++;resetDetected=false;phone=raw;localStorage.setItem('korko-phone',phone);await park();show('ready')});
  $('arm-button').addEventListener('click',async()=>{if(resetDetected||armSubmitting)return;armSubmitting=true;$('arm-button').disabled=true;$('arm-error').textContent='';try{if(!cloudAvailable){await offlineArm();return}let r,d;try{r=await fetch('/api/arme',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client:phone,station:'A',identifiant:id,generation})});d=await r.json()}catch(_){cloudAvailable=false;updateOfflineControls();if(validOfflineAuthorization())await offlineArm();return}if(d.generation!==undefined&&d.generation!==generation){generation=d.generation;resetCustomer();return}if(!r.ok)throw Error(d.erreur);if(d.offline_authorization){const bits=d.offline_authorization.split('.'),payload=JSON.parse(atob(bits[0].replace(/-/g,'+').replace(/_/g,'/')));localStorage.setItem('korko-offline-authorization',JSON.stringify({token:d.offline_authorization,authorization_id:payload.authorization_id,station_id:payload.station_id,expires:payload.expires}))}render(d);polling()}catch(e){$('arm-error').textContent=e.message==='Plus de planche disponible à la station A.'?'Aucune planche ne peut être réservée pour le moment.':'Connexion temporairement indisponible. Réessayez lorsque le service sera accessible.'}finally{armSubmitting=false;if(!resetDetected&&cloudAvailable)$('arm-button').disabled=false}});
  async function offlineArm(){const auth=validOfflineAuthorization();if(!auth){updateOfflineControls();return}try{const r=await fetch(LOCAL_API+'/offline/arme',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({authorization:auth.token,station:auth.station_id})});const d=await r.json();if(!r.ok)throw Error(d.erreur||'Location indisponible.');render({etat:'armée',balise:d.balise,session_id:d.offline_reservation_id,generation});polling()}catch(_){$('arm-error').textContent='Connexion limitée : la station locale est temporairement indisponible.'}}
  $('photo-button').addEventListener('click',()=>$('photo-input').click());

  $('photo-input').addEventListener('change',()=>{
    const file=$('photo-input').files[0];
    clearPhoto(true);
    if(!file)return;
    photoTaskId=crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random();
    photoFailure=false;
    if(!['image/jpeg','image/png','image/webp'].includes(file.type)){
      $('photo-error').textContent='Choisissez une photo JPEG, PNG ou WebP.';
      return;
    }
    if(!file.size||file.size>8*1024*1024){
      $('photo-error').textContent='La photo doit faire moins de 8 Mo.';
      return;
    }
    photoPreviewUrl=URL.createObjectURL(file);
    $('photo-preview').src=photoPreviewUrl;
    $('photo-preview').hidden=false;
    $('photo-note').textContent='Photo choisie : '+file.name;
    $('submit-photo').disabled=false;
  });

  function lirePhoto(file){
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onload=()=>resolve(reader.result);
      reader.onerror=()=>reject(Error('Lecture de la photo impossible. Choisissez une autre image.'));
      reader.readAsDataURL(file);
    });
  }

  async function verifierServeurPhoto(){
    let response;
    try{
      response=await fetch('/api/capabilities',{cache:'no-store'});
    }catch(_){
      throw Error('Connexion au cloud impossible. Vérifiez que mon_cloud.py est lancé sur le port 9000.');
    }
    const data=await response.json().catch(()=>null);
    if(!response.ok||!data||data.photo_verification!==true||data.version!=='photo-queue-1'){
      throw Error('Le cloud en cours d’exécution est trop ancien. Arrêtez-le puis relancez mon_cloud.py.');
    }
  }

  $('submit-photo').addEventListener('click',async()=>{
    const file=$('photo-input').files[0];
    if(!file||photoSubmitting||!receiptSession)return;
    photoSubmitting=true;
    const requestedId=id,requestedSession=receiptSession;
    $('photo-error').textContent='';
    $('photo-progress').hidden=false;
    $('submit-photo').disabled=true;
    $('submit-photo').textContent='Analyse en cours…';
    $('photo-button').disabled=true;
    $('skip-photo').disabled=true;
    try{
      await verifierServeurPhoto();
      const image=await lirePhoto(file);
      let response;
      try{
        response=await fetch('/api/photo-verification',{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            identifiant:requestedId,
            session_id:requestedSession,
            task_id:photoTaskId,
            generation,
            image
          })
        });
      }catch(_){
        throw Error('Envoi interrompu. Vérifiez que le cloud fonctionne et réessayez.');
      }
      const data=await response.json().catch(()=>({}));
      if(requestedId!==id||requestedSession!==receiptSession)return;
      if(data.generation!==undefined&&data.generation!==generation){
        generation=data.generation;
        resetCustomer();
        return;
      }
      if(!response.ok)throw Error(data.erreur||'Analyse impossible. Réessayez.');
      if(!data.tache)throw Error('Le cloud n’a pas confirmé la réception de la photo. Réessayez.');
      photoTaskId=data.tache.task_id;
      retryingPhotoSession=null;
      renderPhotoTask(data.tache);
    }catch(error){
      if(requestedId===id&&requestedSession===receiptSession){
        $('photo-error').textContent=error.message||'Envoi impossible. Vérifiez votre connexion et réessayez.';
      }
    }finally{
      photoSubmitting=false;
      $('photo-progress').hidden=true;
      $('submit-photo').textContent='Analyser la photo';
      $('submit-photo').disabled=!$('photo-input').files[0];
      $('photo-button').disabled=false;
      $('skip-photo').disabled=false;
    }
  });

  $('retry-photo').addEventListener('click',()=>{
    retryingPhotoSession=receiptSession;
    if(!photoFailure||!$('photo-input').files[0])clearPhoto();
    show('receipt');
    if(!$('photo-input').files[0])$('photo-input').click();
  });
  document.querySelectorAll('[data-condition]').forEach(button=>button.addEventListener('click',()=>{if(conditionSubmitting)return;selectedCondition=button.dataset.condition;document.querySelectorAll('[data-condition]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-checked',String(b===button))});$('submit-condition').disabled=false;$('condition-error').textContent=''}));
  $('submit-condition').addEventListener('click',async()=>{if(!selectedCondition||conditionSubmitting)return;conditionSubmitting=true;$('submit-condition').disabled=true;$('submit-condition').textContent='Envoi…';$('skip-condition').disabled=true;try{const r=await fetch('/api/condition',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifiant:id,session_id:receiptSession,condition:selectedCondition,photo:$('photo-input').files[0]?.name||null})}),d=await r.json();if(!r.ok)throw Error(d.erreur);$('condition-form').hidden=true;$('condition-thanks').hidden=false}catch(_){conditionSubmitting=false;$('submit-condition').disabled=!selectedCondition;$('submit-condition').textContent="Valider l'état";$('skip-condition').disabled=false;$('condition-error').textContent='Envoi impossible. Vous pouvez réessayer ou passer.'}});
  $('skip-condition').addEventListener('click',()=>{dismissSession(receiptSession);receiptSession=null;$('condition-form').hidden=true;show('landing')});
  park();state().then(polling);
})();
