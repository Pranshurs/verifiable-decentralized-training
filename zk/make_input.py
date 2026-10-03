"""Reference implementation of the circuit's step, and the witness input for one example.

    python zk/make_input.py > zk/build/input.json            # honest step
    python zk/make_input.py --cheat > zk/build/bad.json       # w_next off by one
"""

from __future__ import annotations

import json
import secrets
import sys

N, F, S, LR_NUM, LR_DEN = 4, 2, 1000, 1, 10
D = LR_DEN * N * S * S


def step(w: list[int], x: list[list[int]], y: list[int]) -> tuple[list[int], list[int]]:
    err = [sum(w[k] * x[i][k] for k in range(F)) - y[i] * S for i in range(N)]
    grad = [sum(err[i] * x[i][j] for i in range(N)) for j in range(F)]
    num = [D * w[j] - LR_NUM * grad[j] for j in range(F)]
    w_next = [n // D for n in num]          # floor, as in the circuit
    r = [n - D * q for n, q in zip(num, w_next)]
    return w_next, r


def main() -> None:
    w = [500, -250]                          # 0.5, -0.25 at scale 1000
    x = [[1000, 2000], [1500, -500], [-1000, 3000], [2500, 1000]]
    y = [1200, 300, -2000, 1750]
    w_next, r = step(w, x, y)
    if "--cheat" in sys.argv:
        w_next = [w_next[0] + 1, w_next[1]]
    salt = lambda: str(secrets.randbits(250))  # noqa: E731
    print(json.dumps({
        "w": [str(v) for v in w], "w_next": [str(v) for v in w_next], "r": [str(v) for v in r],
        "x": [[str(v) for v in row] for row in x], "y": [str(v) for v in y],
        "salt_w": salt(), "salt_next": salt(), "salt_batch": salt(),
    }))
    print(f"w={w} -> w_next={w_next} (scale {S})", file=sys.stderr)


if __name__ == "__main__":
    main()
