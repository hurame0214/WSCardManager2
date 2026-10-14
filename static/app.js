async function officialLookup(){
 const num=document.querySelector("#official_card_number")?.value.trim();
 const name=document.querySelector("#name")?.value.trim();
 if(!num && !name){alert("カード番号またはカード名を入力してください");return}
 const box=document.querySelector("#officialResult"); box.textContent="公式サイトを検索中...";
 try{
  const p=new URLSearchParams(); if(num)p.set("card_number",num); else p.set("name",name);
  const r=await fetch("/api/official?"+p.toString()); const j=await r.json();
  if(!j.ok)throw new Error(j.error);
  const d=j.data;
  for(const [id,key] of [["name","name"],["card_number","card_number"],["title","title"],["rarity","rarity"],["image_url","image_url"]]){
   const el=document.querySelector("#"+id); if(el && d[key]) el.value=d[key];
  }
  box.innerHTML="取得しました。内容を確認して保存してください。"+(d.image_url?'<br><img class="thumb" src="'+d.image_url+'">':"");
 }catch(e){box.textContent="取得できませんでした: "+e.message}
}
async function checkDuplicate(){
 const el=document.querySelector('#card_number');
 const box=document.querySelector('#duplicateNotice');
 if(!el||!box)return false;
 const number=el.value.trim();
 if(!number){box.hidden=true;return false;}
 try{
  const response=await fetch('/api/card-exists?'+new URLSearchParams({card_number:number}));
  const data=await response.json();
  if(data.exists){
   box.hidden=false;
   box.textContent='このカード番号は登録済みです。既存カードの詳細ページへ移動します。';
   window.location.assign(data.url);
   return true;
  }
  box.hidden=true;
 }catch(e){box.hidden=true;}
 return false;
}
document.addEventListener('DOMContentLoaded',()=>{
 const number=document.querySelector('#card_number');
 if(number)number.addEventListener('change',checkDuplicate);
});
