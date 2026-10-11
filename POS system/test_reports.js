const assert=require('node:assert/strict');
const {report,dateKey}=require('./static/reports.js');
const line=(menu,name,price,qty)=>({menu,name,price,qty});
const sale=(at,total,lines,payment='Cash',void_at=null)=>({at,total,snapshot:JSON.stringify(lines),payment,void_at});
const sales=[
  sale('2026-10-11T17:00:00Z',20000,[line(1,'Latte',10000,2)]), // Monday 01:00 Manila
  sale('2026-10-12T10:00:00+08:00',15000,[line(1,'Latte',15000,1)],'E-wallet'),
  sale('2026-10-12T11:00:00+08:00',9999,[line(2,'Cake',9999,1)],'Cash','2026-10-13T08:00:00+08:00'),
  sale('2026-10-18T23:59:00+08:00',5000,[line(2,'Cake',5000,1)]),
  sale('2026-10-19T00:00:00+08:00',9000,[line(3,'Toast',9000,1)])
];
assert.equal(dateKey('2026-10-11T17:00:00Z'),'2026-10-12');
const daily=report(sales,'daily','2026-10-12');
assert.equal(daily.total,35000);assert.equal(daily.count,2);assert.equal(daily.units,3);assert.equal(daily.average,17500);
assert.equal(daily.voidCount,1);assert.equal(daily.voidTotal,9999);assert.equal(daily.payments.Cash,20000);assert.equal(daily.payments['E-wallet'],15000);
assert.equal(daily.products[0].revenue,35000);assert.equal(daily.buckets[1].total,20000);
const weekly=report(sales,'weekly','2026-10-18');
assert.equal(weekly.start,'2026-10-12');assert.equal(weekly.end,'2026-10-18');assert.equal(weekly.total,40000);assert.equal(weekly.count,3);
assert.equal(weekly.buckets.length,7);assert.equal(weekly.buckets[6].total,5000);
const empty=report([],'daily','2026-10-12');assert.equal(empty.average,0);assert.equal(empty.total,0);
const boundary=report([],'weekly','2026-01-01');assert.equal(boundary.start,'2025-12-29');assert.equal(boundary.end,'2026-01-04');
console.log('Report checks passed: timezone, daily/weekly boundaries, voids, historical prices, payments, and empty periods.');
const costed=sales.map(s=>({...s,cogs:1000}));
const costReport=report(costed,'daily','2026-10-12');
assert.equal(costReport.cogs,2000);assert.equal(costReport.profit,33000);assert.equal(costReport.missingCostCount,0);
assert.equal(daily.cogs,null);assert.equal(daily.profit,null);assert.equal(daily.missingCostCount,2);
assert.equal(empty.cogs,0);assert.equal(empty.profit,0);
console.log('COGS/profit checks passed: void exclusion, missing historical costs and zero-sales periods.');
