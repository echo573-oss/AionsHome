document.getElementById('backHome').onclick=()=>location.href='/';
(async()=>{
  const list=document.getElementById('savedMatches');
  const continuations=document.getElementById('continuations');
  try{
    const response=await fetch('/api/billiards',{cache:'no-store'});
    if(response.ok){
      const {games,names,available}=await response.json();
      if(!available)document.getElementById('billiardsStatus').textContent='台球室暂时不可用';
      const active=games.filter(game=>game.status!=='finished');
      if(active.length){
        continuations.hidden=false;
        for(const game of active){
          const link=document.createElement('a');link.className='saved-match';link.href=`/billiards?game=${game.id}`;
          const title=document.createElement('strong');title.textContent=game.players.map(p=>names[p]).join(' & ');
          const status=document.createElement('span');status.textContent=`${game.status==='running'?'正在打球':'已暂停'} · ${game.score.join(':')} · ${game.players.includes('user')?'继续球局':'去旁观'} ›`;
          link.append(title,status);list.append(link);
        }
        document.getElementById('billiardsStatus').textContent=active.some(g=>!g.players.includes('user'))?'伴侣们正在球桌边':'有一局等你回来';
      }
    }
  }catch{} // A missing game module never breaks the lobby.
  try{
    const response=await fetch('/api/spicy-board',{cache:'no-store'});
    if(!response.ok)return;
    const {games,available}=await response.json();
    const status=document.getElementById('spicyBoardStatus');
    if(status && !available)status.textContent='棋盘室暂时不可用';
    const active=(games||[]).filter(game=>game.status!=='finished');
    if(!active.length)return;
    continuations.hidden=false;
    for(const game of active){
      const link=document.createElement('a');link.className='saved-match';link.href=`/spicy-board?game=${game.id}`;
      const title=document.createElement('strong');title.textContent=(game.players||[]).filter(Boolean).join(' 与 ');
      const note=document.createElement('span');note.textContent=`棋盘室 · ${game.turn?('轮到 '+game.turn):'继续这一局'} ›`;
      link.append(title,note);list.append(link);
    }
    if(status && available)status.textContent='有一局等你回来';
  }catch{}
})();
