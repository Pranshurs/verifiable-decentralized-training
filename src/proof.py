import hashlib
import json
from typing import List

def hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def checkpoint_hash(checkpoint: dict) -> str:
    s = json.dumps(checkpoint, sort_keys=True).encode('utf-8')
    return hash_bytes(s)

def merkle_root(hashes: List[str]) -> str:
    if not hashes:
        return ''
    cur = hashes[:]
    while len(cur) > 1:
        next_layer = []
        for i in range(0, len(cur), 2):
            a = cur[i]
            b = cur[i+1] if i+1 < len(cur) else cur[i]
            next_layer.append(hash_bytes((a+b).encode('utf-8')))
        cur = next_layer
    return cur[0]
