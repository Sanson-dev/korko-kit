(() => {
  let id = localStorage.getItem('korko-session') || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()), poller;
  localStorage.setItem('korko-session', id);
  let phone = localStorage.getItem('korko-phone') || '';
  const $ = x => document.getElementById(x), board = x => (x || '').replace('korko-', '');
  const money = x => Number(x || 0).toLocaleString('fr-FR', {minimumFractionDigits:2, maximumFractionDigits:2}) + ' €';
  const duration = x => { x=Math.max(0,Math.floor(x||0)); return String(Math.floor(x/60)).padStart(2,'0')+':'+String(x%60).padStart(2,'0'); };
  function show(x){document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));$(x).classList.add('active')}
  async function park(){const d=await (await fetch('/api/parc')).json();$('board-count').textContent=Object.values(d.planches).filter(p=>p.ou==='A'&&p.statut==='au râtelier').length}
  function render(d){if(d.etat==='armée'){$('board-name').textContent=board(d.balise);show('armed')}if(d.etat==='en cours'){$('active-board').textContent=board(d.balise);$('duration').textContent=duration(d.duree);$('current-price').textContent=money(d.montant);show('active')}if(d.etat==='retournée'){$('final-duration').textContent=duration(d.duree);$('final-price').textContent=money(d.montant);show('receipt')}}
  async function state(){try{render(await (await fetch('/api/client?identifiant='+encodeURIComponent(id))).json())}catch(_){}}
  function polling(){clearInterval(poller);state();poller=setInterval(state,1200)}
  document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',async()=>{if(b.dataset.go==='ready'){await park();clearInterval(poller)}show(b.dataset.go)}));
  $('phone-input').value=phone;
  $('phone-form').addEventListener('submit',async e=>{e.preventDefault();const raw=$('phone-input').value.replace(/[^0-9+]/g,'');if(raw.replace(/\D/g,'').length<8){$('phone-error').textContent='Entrez un numéro valide.';return}phone=raw;localStorage.setItem('korko-phone',phone);await park();show('ready')});
  $('arm-button').addEventListener('click',async()=>{$('arm-error').textContent='';try{const r=await fetch('/api/arme',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client:phone,station:'A',identifiant:id})}),d=await r.json();if(!r.ok)throw Error(d.erreur);render(d);polling()}catch(e){$('arm-error').textContent=e.message||'Impossible de réserver une planche.'}});
  park();state().then(polling);
})();
