pragma circom 2.0.0;

// EXPERIMENTAL. Proves that one fixed-point gradient step on a private minibatch was
// computed correctly, without revealing the weights or the data.
//
//   w_next[j] = floor((D * w[j] - LR_NUM * grad[j]) / D)
//   grad[j]   = sum_i (sum_k w[k] * x[i][k] - y[i] * S) * x[i][j]
//   D         = LR_DEN * N * S^2
//
// Linear regression (squared loss) on N rows and F features. All values are integers at
// fixed-point scale S. Public inputs are salted Poseidon commitments to the previous
// weights, the next weights and the batch, so a verifier learns only that the committed
// w_next follows from the committed w and batch.
//
// This is NOT the MVP workload (logistic regression needs a sigmoid, which is expensive
// in a circuit) and NOT a proof of a whole training run. See docs/ZK_EXPERIMENT.md.

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/bitify.circom";
include "circomlib/circuits/comparators.circom";

template SgdStep(N, F, S, LR_NUM, LR_DEN, RBITS) {
    signal input w[F];
    signal input w_next[F];
    signal input x[N][F];
    signal input y[N];
    signal input r[F];          // floor-division remainders, 0 <= r < D
    signal input salt_w;
    signal input salt_next;
    signal input salt_batch;

    signal output commit_w;
    signal output commit_next;
    signal output commit_batch;

    var D = LR_DEN * N * S * S;

    // residuals at scale S^2
    signal prod[N][F];
    signal err[N];
    for (var i = 0; i < N; i++) {
        var acc = 0;
        for (var k = 0; k < F; k++) {
            prod[i][k] <== w[k] * x[i][k];
            acc += prod[i][k];
        }
        err[i] <== acc - y[i] * S;
    }

    // gradient at scale S^3, then the floor-rounded update with a range-checked remainder
    signal g[F][N];
    component rbits[F];
    component rlt[F];
    for (var j = 0; j < F; j++) {
        var grad = 0;
        for (var i = 0; i < N; i++) {
            g[j][i] <== err[i] * x[i][j];
            grad += g[j][i];
        }
        D * w_next[j] + r[j] === D * w[j] - LR_NUM * grad;
        rbits[j] = Num2Bits(RBITS);
        rbits[j].in <== r[j];
        rlt[j] = LessThan(RBITS);
        rlt[j].in[0] <== r[j];
        rlt[j].in[1] <== D;
        rlt[j].out === 1;
    }

    component hw = Poseidon(F + 1);
    component hn = Poseidon(F + 1);
    for (var j = 0; j < F; j++) {
        hw.inputs[j] <== w[j];
        hn.inputs[j] <== w_next[j];
    }
    hw.inputs[F] <== salt_w;
    hn.inputs[F] <== salt_next;
    commit_w <== hw.out;
    commit_next <== hn.out;

    component hb = Poseidon(N * F + N + 1);
    for (var i = 0; i < N; i++) {
        for (var k = 0; k < F; k++) {
            hb.inputs[i * F + k] <== x[i][k];
        }
        hb.inputs[N * F + i] <== y[i];
    }
    hb.inputs[N * F + N] <== salt_batch;
    commit_batch <== hb.out;
}

// N=4 rows, F=2 features, scale 1000, learning rate 1/10; D = 4e7 < 2^26.
component main = SgdStep(4, 2, 1000, 1, 10, 26);
