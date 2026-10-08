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