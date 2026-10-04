#!/usr/bin/env bash
# EXPERIMENTAL Groth16 proof of one fixed-point SGD step (see docs/ZK_EXPERIMENT.md).
# Needs Node 18+. Installs circom2/snarkjs/circomlib into zk/node_modules on first run.
set -euo pipefail
cd "$(dirname "$0")"
# A local package.json pins npm's install prefix to this directory (otherwise npm walks up
# and installs into, or replaces, a parent node_modules).
[ -f package.json ] || echo '{"private": true}' > package.json
[ -d node_modules ] || npm install --silent circom2@0.2.23 snarkjs@0.7.6 circomlib@2.0.5
B=build; mkdir -p $B
sj() { npx snarkjs "$@"; }
npx circom2 sgd_step.circom --r1cs --wasm -o $B -l node_modules
# Local, single-contributor trusted setup: fine for an experiment, NOT for production.
sj powersoftau new bn128 12 $B/pot_0.ptau
sj powersoftau contribute $B/pot_0.ptau $B/pot_1.ptau --name=local -e="$(openssl rand -hex 32)"
sj powersoftau prepare phase2 $B/pot_1.ptau $B/pot_final.ptau
sj groth16 setup $B/sgd_step.r1cs $B/pot_final.ptau $B/k0.zkey
sj zkey contribute $B/k0.zkey $B/k1.zkey --name=local -e="$(openssl rand -hex 32)"
sj zkey export verificationkey $B/k1.zkey $B/vkey.json
python3 make_input.py > $B/input.json
node $B/sgd_step_js/generate_witness.js $B/sgd_step_js/sgd_step.wasm $B/input.json $B/w.wtns
time sj groth16 prove $B/k1.zkey $B/w.wtns $B/proof.json $B/public.json
time sj groth16 verify $B/vkey.json $B/public.json $B/proof.json
echo "--- cheating witness (w_next off by one) must fail:"
python3 make_input.py --cheat > $B/bad.json
if node $B/sgd_step_js/generate_witness.js $B/sgd_step_js/sgd_step.wasm $B/bad.json $B/bad.wtns 2>/dev/null; then
  echo "UNEXPECTED: cheating witness accepted"; exit 1; else echo "rejected (constraint unsatisfied)"; fi
echo "--- honest proof with a different claimed w_next commitment must fail:"
python3 -c "import json;p=json.load(open('$B/public.json'));p[1]=str(int(p[1])+1);json.dump(p,open('$B/public_bad.json','w'))"
if sj groth16 verify $B/vkey.json $B/public_bad.json $B/proof.json; then echo "UNEXPECTED"; exit 1; else echo "rejected"; fi
