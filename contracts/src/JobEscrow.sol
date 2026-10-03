// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @title JobEscrow
/// @notice Holds a coder's payment for one compute job until an independent verifier
///         accepts the provider's committed result. Plain ETH (test ETH on a local chain);
///         there is no token.
///
/// Lifecycle (STESH white paper §4.2: escrow -> execution -> verification -> release):
///
///   fund (coder) -> Funded -> assign (coordinator) -> Assigned
///     -> submitResult (provider, binds resultHash) -> Submitted
///     -> verify (verifier, same resultHash) -> Verified | Rejected
///   Verified -> release (coder or verifier, never the provider) -> Released
///   Funded / Rejected / Assigned past deadline / Submitted past verify window -> refund -> Refunded
///
/// Guarantees enforced here:
///   - the provider can't release, verify or reassign anything;
///   - payment moves only after the verifier accepted the exact hash the provider submitted;
///   - each job pays out at most once (state moves before the transfer);
///   - a rejected result never pays the provider.
/// Not enforced here: whether the verifier's judgement is right. That is the off-chain
/// verifier's job (see docs/VERIFICATION.md); the contract trusts the verifier address the
/// coder chose when funding.
contract JobEscrow {
    enum State { None, Funded, Assigned, Submitted, Verified, Released, Rejected, Refunded }

    struct Job {
        address coder;
        address provider;
        address verifier;
        uint256 amount;
        uint64 deadline;     // provider must submit before this
        State state;
        bytes32 resultHash;  // provider's commitment (manifest Merkle root)
    }

    address public immutable coordinator;
    uint64 public immutable verifyWindow; // seconds after deadline before an unverified job can be refunded
    mapping(bytes32 => Job) public jobs;

    event Funded(bytes32 indexed jobId, address indexed coder, address verifier, uint256 amount, uint64 deadline);
    event Assigned(bytes32 indexed jobId, address indexed provider);
    event ResultSubmitted(bytes32 indexed jobId, bytes32 resultHash);
    event Verified(bytes32 indexed jobId, bytes32 resultHash, bool accepted);
    event Released(bytes32 indexed jobId, address indexed provider, uint256 amount);
    event Refunded(bytes32 indexed jobId, address indexed coder, uint256 amount);

    error BadState(State actual);
    error NotAllowed();
    error InvalidArgument();
    error TooLate();
    error TooEarly();
    error HashMismatch();
    error TransferFailed();

    constructor(address coordinator_, uint64 verifyWindow_) {
        if (coordinator_ == address(0)) revert InvalidArgument();
        coordinator = coordinator_;
        verifyWindow = verifyWindow_;
    }

    /// @param jobId keccak256 of the coder-signed job specification.
    function fund(bytes32 jobId, address verifier, uint64 deadline) external payable {
        Job storage j = jobs[jobId];
        if (j.state != State.None) revert BadState(j.state);
        if (msg.value == 0 || verifier == address(0) || verifier == msg.sender || jobId == bytes32(0)) {
            revert InvalidArgument();
        }
        if (deadline <= block.timestamp) revert TooLate();
        jobs[jobId] = Job(msg.sender, address(0), verifier, msg.value, deadline, State.Funded, bytes32(0));
        emit Funded(jobId, msg.sender, verifier, msg.value, deadline);
    }

    /// @notice Coordinator assigns a matched provider. Also used to reassign after a provider
    ///         fails, as long as no result was submitted.
    function assign(bytes32 jobId, address provider) external {
        if (msg.sender != coordinator) revert NotAllowed();
        Job storage j = jobs[jobId];
        if (j.state != State.Funded && j.state != State.Assigned) revert BadState(j.state);
        if (provider == address(0) || provider == j.coder || provider == j.verifier) revert InvalidArgument();
        if (block.timestamp >= j.deadline) revert TooLate();
        j.provider = provider;
        j.state = State.Assigned;
        emit Assigned(jobId, provider);
    }

    function submitResult(bytes32 jobId, bytes32 resultHash) external {
        Job storage j = jobs[jobId];
        if (j.state != State.Assigned) revert BadState(j.state);
        if (msg.sender != j.provider) revert NotAllowed();
        if (block.timestamp >= j.deadline) revert TooLate();
        if (resultHash == bytes32(0)) revert InvalidArgument();
        j.resultHash = resultHash;
        j.state = State.Submitted;
        emit ResultSubmitted(jobId, resultHash);
    }

    /// @notice The verdict names the hash it judged, so it can't be applied to a different result.
    function verify(bytes32 jobId, bytes32 resultHash, bool accepted) external {
        Job storage j = jobs[jobId];
        if (j.state != State.Submitted) revert BadState(j.state);
        if (msg.sender != j.verifier) revert NotAllowed();
        if (resultHash != j.resultHash) revert HashMismatch();
        j.state = accepted ? State.Verified : State.Rejected;
        emit Verified(jobId, resultHash, accepted);
    }

    function release(bytes32 jobId) external {
        Job storage j = jobs[jobId];
        if (j.state != State.Verified) revert BadState(j.state);
        if (msg.sender != j.coder && msg.sender != j.verifier) revert NotAllowed();
        j.state = State.Released; // effects before interaction: a re-entrant call sees Released
        uint256 amount = j.amount;
        (bool ok,) = j.provider.call{value: amount}("");
        if (!ok) revert TransferFailed();
        emit Released(jobId, j.provider, amount);
    }

    /// @notice Return the coder's funds when the job can no longer be paid:
    ///         never assigned, result rejected, provider missed the deadline, or the
    ///         verifier stayed silent past the verify window.
    function refund(bytes32 jobId) external {
        Job storage j = jobs[jobId];
        if (msg.sender != j.coder) revert NotAllowed();
        State s = j.state;
        if (s == State.Assigned) {
            if (block.timestamp < j.deadline) revert TooEarly();
        } else if (s == State.Submitted) {
            if (block.timestamp < uint256(j.deadline) + verifyWindow) revert TooEarly();
        } else if (s != State.Funded && s != State.Rejected) {
            revert BadState(s);
        }
        j.state = State.Refunded;
        uint256 amount = j.amount;
        (bool ok,) = j.coder.call{value: amount}("");
        if (!ok) revert TransferFailed();
        emit Refunded(jobId, j.coder, amount);
    }
}
