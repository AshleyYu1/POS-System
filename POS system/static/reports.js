/* Shared report calculations. Dates use the café's Philippine timezone. */
(function(root){
  const dateKey=t=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Manila',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t));
  const shift=(day,n)=>{const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)};
  function report(sales,mode,day){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||!Number.isFinite(Date.parse(day)))throw Error('Choose a valid report date.');
    const weekday=new Date(day+'T12:00:00Z').getUTCDay();
    const start=mode==='weekly'?shift(day,-((weekday+6)%7)):day;
    const end=mode==='weekly'?shift(start,6):day;
    const included=sales.filter(s=>{const key=dateKey(s.at);return key>=start&&key<=end});
    const completed=included.filter(s=>!s.void_at), voided=included.filter(s=>s.void_at);
    const buckets=mode==='weekly'?Array.from({length:7},(_,i)=>({key:shift(start,i),total:0,count:0})):Array.from({length:24},(_,i)=>({key:String(i).padStart(2,'0'),total:0,count:0}));
    const products=new Map(),payments={Cash:0,'E-wallet':0};
    for(const s of completed){
      const key=mode==='weekly'?dateKey(s.at):new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Manila',hour:'2-digit',hourCycle:'h23'}).format(new Date(s.at));
      const bucket=buckets.find(b=>b.key===key);if(bucket){bucket.total+=s.total;bucket.count++}
      payments[s.payment]=(payments[s.payment]||0)+s.total;
      for(const l of JSON.parse(s.snapshot)){
        const pkey=String(l.menu)+':'+l.name;
        const p=products.get(pkey)||{name:l.name,quantity:0,revenue:0};
        p.quantity+=l.qty;p.revenue+=l.qty*l.price;products.set(pkey,p);
      }
    }
    const total=completed.reduce((a,s)=>a+s.total,0);
    const missingCostCount=completed.filter(s=>s.cogs==null).length;
    const cogs=missingCostCount?null:completed.reduce((a,s)=>a+s.cogs,0);
    return {start,end,total,cogs,profit:cogs==null?null:total-cogs,missingCostCount,count:completed.length,average:completed.length?Math.round(total/completed.length):0,
      units:[...products.values()].reduce((a,p)=>a+p.quantity,0),payments,buckets,
      products:[...products.values()].sort((a,b)=>b.revenue-a.revenue),completed,
      voidCount:voided.length,voidTotal:voided.reduce((a,s)=>a+s.total,0)};
  }
  const api={report,dateKey,shift};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.Reports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
