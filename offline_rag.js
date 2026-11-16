// Local evidence retrieval plus a fixed Version 8-L byte-mode QR encoder.
const indexEvidence = (rows, query='') => {
 const words=query.toLowerCase().match(/[\p{L}\p{N}]+/gu)||[];
 return rows.map(row=>({row,rank:words.filter(word=>row.join(' ').toLowerCase().includes(word)).length}))
  .sort((a,b)=>b.rank-a.rank).map(hit=>hit.row);
};
function qrSVG(text){
 const bytes=new TextEncoder().encode(text), size=49;
 if(bytes.length>191) throw new Error('QR payload exceeds 191 bytes');
 const bits=[];
 const push=(value,count)=>{for(let i=count-1;i>=0;i--)bits.push((value>>>i)&1);};
 push(7,4);push(26,8); // ECI 26 names UTF-8.
 push(4,4);push(bytes.length,8);bytes.forEach(b=>push(b,8));
 push(0,Math.min(4,1552-bits.length));while(bits.length%8)bits.push(0);
 const data=[];
 for(let i=0;i<bits.length;i+=8)data.push(bits.slice(i,i+8).reduce((a,b)=>2*a+b,0));
 for(let i=0;data.length<194;i++)data.push(i%2?17:236);
 const mul=(a,b)=>{let z=0;while(b){if(b&1)z^=a;a=(a<<1)^((a&128)?285:0);b>>=1;}return z;};
 let gen=[1],root=1;
 for(let j=0;j<24;j++){
   const next=Array(gen.length+1).fill(0);
   gen.forEach((c,i)=>{next[i]^=c;next[i+1]^=mul(c,root);});
   gen=next;root=mul(root,2);
 }
 const blocks=[data.slice(0,97),data.slice(97)];
 const ecc=blocks.map(block=>{
   const buf=[...block,...Array(24).fill(0)];
   for(let i=0;i<97;i++){const coefficient=buf[i];gen.forEach((g,j)=>buf[i+j]^=mul(g,coefficient));}
   return buf.slice(-24);
 });
 bits.length=0;
 for(let i=0;i<97;i++)blocks.forEach(b=>push(b[i],8));
 for(let i=0;i<24;i++)ecc.forEach(b=>push(b[i],8));
 const m=Array.from({length:size},()=>Array(size).fill(null));
 const put=(x,y,value)=>{if(x>=0&&y>=0&&x<size&&y<size)m[y][x]=Number(!!value);};
 for(let i=0;i<size;i++){put(6,i,i%2===0);put(i,6,i%2===0);}
 for(const [x,y] of [[3,3],[size-4,3],[3,size-4]])
   for(let j=-4;j<=4;j++)for(let i=-4;i<=4;i++){
     const d=Math.max(Math.abs(i),Math.abs(j));put(x+i,y+j,d!==2&&d!==4);
   }
 for(const y of [6,24,42])for(const x of [6,24,42]){
   if((x===6&&y===6)||(x===42&&y===6)||(x===6&&y===42))continue;
   for(let j=-2;j<=2;j++)for(let i=-2;i<=2;i++)put(x+i,y+j,Math.max(Math.abs(i),Math.abs(j))!==1);
 }
 const format=0x77c4; // ECC L, mask 0.
 const bit=(v,i)=>(v>>>i)&1;
 for(let i=0;i<6;i++)put(8,i,bit(format,i));
 put(8,7,bit(format,6));put(8,8,bit(format,7));put(7,8,bit(format,8));
 for(let i=9;i<15;i++)put(14-i,8,bit(format,i));
 for(let i=0;i<8;i++)put(size-1-i,8,bit(format,i));
 for(let i=8;i<15;i++)put(8,size-15+i,bit(format,i));
 put(8,size-8,1);
 for(let i=0;i<18;i++){put(size-11+i%3,Math.floor(i/3),bit(0x085bc,i));put(Math.floor(i/3),size-11+i%3,bit(0x085bc,i));}
 let k=0,up=true;
 for(let right=size-1;right>=1;right-=2){
   if(right===6)right=5;
   for(let j=0;j<size;j++){const y=up?size-1-j:j;
     for(let x=right;x>right-2;x--)if(m[y][x]===null)put(x,y,(bits[k++]||0)^((x+y)%2===0));
   }
   up=!up;
 }
 const paths=m.flatMap((row,y)=>row.map((dark,x)=>dark?'M'+(x+4)+' '+(y+4)+'h1v1h-1z':'')).join('');
 return '<svg class="qr" viewBox="0 0 57 57" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="QR" shape-rendering="crispEdges"><path fill="white" d="M0 0h57v57H0z"/><path fill="black" d="'+paths+'"/></svg>';
}

