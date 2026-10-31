'use strict';
// Fixed Champions master only. This module deliberately has no Showdown import.
const fs=require('node:fs');
const path=require('node:path');
const master=path.join(process.env.CHAMPIONS_MASTERS_DIR||path.join(__dirname,'masters'),
  'Pokemon_Champions_MC_move_master.csv');
function firstThree(line){
  const fields=[];let value='',quoted=false;
  for(let i=0;i<line.length&&fields.length<3;i++){
    const c=line[i];
    if(c==='"'){
      if(quoted&&line[i+1]==='"'){value+='"';i++;}
      else quoted=!quoted;
    }else if(c===','&&!quoted){fields.push(value);value='';}
    else value+=c;
  }
  if(fields.length!==3||quoted)throw Error('invalid Champions move master row');
  return fields;
}
const aliases=new Map(),keys=new Set();
const lines=fs.readFileSync(master,'utf8').replace(/^\uFEFF/,'').split(/\r?\n/).filter(Boolean);
if(firstThree(lines.shift()).join('|')!=='MoveKey|技名|英語名')
  throw Error('unexpected Champions move master columns');
for(const line of lines){
  const [key,ja,en]=firstThree(line);
  if(!/^[a-z0-9]+$/.test(key)||!ja||!en||keys.has(key))throw Error('invalid move master key');
  keys.add(key);
  for(const alias of [key,ja,en]){
    const norm=/^[\x00-\x7F]+$/.test(alias)?alias.toLowerCase().replace(/[^a-z0-9]/g,''):alias;
    if(aliases.has(norm)&&aliases.get(norm)!==key)throw Error('ambiguous Champions move alias');
    aliases.set(norm,key);
  }
}
function moveKey(input){
  if(typeof input!=='string'||!input.trim())return null;
  const s=input.trim();
  const norm=/^[\x00-\x7F]+$/.test(s)?s.toLowerCase().replace(/[^a-z0-9]/g,''):s;
  return aliases.get(norm)||null;
}
module.exports={moveKey,keys};
