import argparse
import time
import threading
from src.node import Node
from src.proof import merkle_root
import json

class Orchestrator:
    def __init__(self, n_nodes=3):
        self.nodes = [Node(i) for i in range(n_nodes)]
        self.history = []

    def train_round(self, epochs=3):
        for e in range(1, epochs+1):
            threads = []
            for node in self.nodes:
                t = threading.Thread(target=node.local_step)
                t.start()
                threads.append(t)
            for t in threads:
                t.join()

            ckpts = [node.checkpoint(e) for node in self.nodes]
            hashes = [c['hash'] for c in ckpts]
            root = merkle_root(hashes)
            round_record = {
                'epoch': e,
                'root': root,
                'checkpoints': ckpts
            }
            self.history.append(round_record)
            print(f"Epoch {e} -> Merkle root: {root}")
            time.sleep(0.5)

    def save_history(self, path='history.json'):
        with open(path, 'w') as f:
            json.dump(self.history, f, indent=2)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--nodes', type=int, default=3)
    parser.add_argument('--epochs', type=int, default=3)
    args = parser.parse_args()

    orch = Orchestrator(n_nodes=args.nodes)
    orch.train_round(epochs=args.epochs)
    orch.save_history()
    print('Training simulation complete. History saved to history.json')
