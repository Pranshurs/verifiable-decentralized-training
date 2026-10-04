import time
import numpy as np
from src.proof import checkpoint_hash

class Node:
    def __init__(self, node_id: int):
        self.node_id = node_id
        self.state = np.random.randn(10)

    def local_step(self):
        self.state += 0.01 * np.random.randn(10)

    def checkpoint(self, epoch: int):
        weights = self.state.tolist()
        ckpt = {
            'node_id': self.node_id,
            'epoch': epoch,
            'weights_sum': float(sum(weights)),
            'weights_len': len(weights),
            'timestamp': int(time.time())
        }
        ckpt_hash = checkpoint_hash(ckpt)
        ckpt['hash'] = ckpt_hash
        return ckpt
