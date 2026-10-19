import './style.css';
import {defaults,estimate} from './cost-model.ts';
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(n);
const wrapper=document.getElementById('planning-estimates')!;
const heading=document.createElement('h3');heading.textContent='Estimated budget for this intake';wrapper.append(heading);
const grid=document.createElement('div');grid.className='platform-grid';
for(const item of estimate(defaults).comparisons){
 const card=document.createElement('article'),title=document.createElement('h4'),body=document.createElement('p');
 title.textContent=item.name;body.textContent=`${money(item.monthly)}/month + ${money(item.setup)} setup. First year: ${money(item.monthly*12+item.setup)}.`;
 card.append(title,body);grid.append(card);
}wrapper.append(grid);
