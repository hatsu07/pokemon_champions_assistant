'use strict';
// Documentation-derived arithmetic reference. Zero dependency on Showdown.
// This is not a Champions-device equivalence certificate or battle turn emulator.
const RULE_VERSION = 'champions-wiki-damage-reference-v1';
const SOURCES = Object.freeze({
  damage_order: 'https://pkmn-cp-dataportal.com/battle/damage/damage-order/',
  damage_formula: 'https://champions.pokewiki.net/ダメージ計算式',
});
function invalid(message) { throw new RangeError(message); }
function positiveInteger(value, label) {
  if (!Number.isSafeInteger(value) || value < 1) invalid(label + ' must be a positive safe integer');
}
function ratio(value, label, allowedZero = false) {
  if (!Array.isArray(value) || value.length !== 2 || value.some(x => !Number.isSafeInteger(x)) ||
      value[0] < (allowedZero ? 0 : 1) || value[1] < 1 || value[0] > 100000 || value[1] > 100000)
    invalid(label + ' must be an integer [numerator, denominator] ratio');
  return [BigInt(value[0]), BigInt(value[1])];
}
function floorRatio(value, [n, d]) { return (value * n) / d; }
// Japanese "五捨五超入": the fractional half is rounded down, not up.
function halfDownRatio(value, [n, d]) {
  const total = value * n;
  const whole = total / d;
  return whole + ((total % d) * 2n > d ? 1n : 0n);
}
function calculate(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) invalid('input object required');
  const required = ['attack', 'defense', 'basePower', 'powerMultiplier', 'weatherMultiplier',
    'critical', 'stabMultiplier', 'typeMultiplier', 'otherMultiplier'];
  for (const k of Object.keys(input)) if (!required.includes(k)) invalid('unexpected field: ' + k);
  for (const k of required) if (!Object.hasOwn(input, k)) invalid('missing condition: ' + k);
  for (const k of ['attack','defense','basePower']) positiveInteger(input[k],k);
  if (typeof input.critical !== 'boolean') invalid('critical must be boolean');
  const power=ratio(input.powerMultiplier,'powerMultiplier');
  const weather=ratio(input.weatherMultiplier,'weatherMultiplier');
  const stab=ratio(input.stabMultiplier,'stabMultiplier');
  const effectiveness=ratio(input.typeMultiplier,'typeMultiplier',true);
  const other=ratio(input.otherMultiplier,'otherMultiplier');
  // 22 is the fixed arithmetic coefficient from the cited example, not a game-observed level.
  const p=floorRatio(BigInt(input.basePower),power);
  const base=(22n*p*BigInt(input.attack)/BigInt(input.defense))/50n+2n;
  const rolls=[];
  for(let pct=85;pct<=100;pct++) {
    let damage=halfDownRatio(base,weather);
    if(input.critical)damage=halfDownRatio(damage,[3n,2n]);
    damage=floorRatio(damage,[BigInt(pct),100n]);
    damage=halfDownRatio(damage,stab);
    damage=floorRatio(damage,effectiveness);
    if(effectiveness[0]===0n)damage=0n;
    else damage=halfDownRatio(damage,other);
    if(effectiveness[0]!==0n&&damage<1n)damage=1n;
    if(damage>BigInt(Number.MAX_SAFE_INTEGER))invalid('calculated result exceeds safe integer');
    rolls.push(Number(damage));
  }
  return {status:'WIKI_ARITHMETIC_ONLY',rule_version:RULE_VERSION,game_observed_level:false,
    ruleset_source_ids:Object.keys(SOURCES),rolls,minimum:rolls[0],maximum:rolls[15]};
}
module.exports={calculate,halfDownRatio,SOURCES,RULE_VERSION};
if(require.main===module){
  try{const fs=require('node:fs');if(process.argv.length!==3)throw Error('Usage: node wiki_damage_reference.cjs INPUT.json');
    process.stdout.write(JSON.stringify(calculate(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))),null,2)+'\n');
  }catch(e){process.stdout.write(JSON.stringify({status:'NOT_RUN',reason:e.message})+'\n');process.exitCode=2;}
}
