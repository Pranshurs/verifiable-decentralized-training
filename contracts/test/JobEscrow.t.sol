// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Test} from "forge-std/Test.sol";
import {JobEscrow} from "../src/JobEscrow.sol";

/// A provider that tries to re-enter release() when paid.
contract ReentrantProvider {
    JobEscrow public escrow;
    bytes32 public jobId;
    bool public reentered;

    constructor(JobEscrow e) { escrow = e; }

    function setJob(bytes32 id) external { jobId = id; }

    function submit(bytes32 h) external { escrow.submitResult(jobId, h); }

    receive() external payable {
        if (!reentered) {
            reentered = true;
            try escrow.release(jobId) {} catch {}
            try escrow.refund(jobId) {} catch {}
        }
    }
}

/// A coder contract that re-enters refund() when its refund arrives.
contract ReentrantCoder {
    JobEscrow public escrow;
    bytes32 public jobId;
    uint256 public reentries;

    constructor(JobEscrow e) { escrow = e; }

    function fund(bytes32 id, address verifier, uint64 deadline) external payable {
        jobId = id;
        escrow.fund{value: msg.value}(id, verifier, deadline);
    }

    function refund() external { escrow.refund(jobId); }

    receive() external payable {
        if (reentries < 3) {
            reentries++;
            try escrow.refund(jobId) {} catch {}
        }
    }
}

contract JobEscrowTest is Test {
    JobEscrow escrow;
    address coordinator = makeAddr("coordinator");
    address coder = makeAddr("coder");
    address provider = makeAddr("provider");
    address verifier = makeAddr("verifier");
    address stranger = makeAddr("stranger");
    bytes32 constant JOB = keccak256("job-1");
    bytes32 constant RESULT = keccak256("result-1");
    uint256 constant PAY = 1 ether;
    uint64 constant WINDOW = 1 days;
    uint64 deadline;

    function setUp() public {
        escrow = new JobEscrow(coordinator, WINDOW);
        vm.deal(coder, 10 ether);
        deadline = uint64(block.timestamp + 1 hours);
    }

    function _state(bytes32 id) internal view returns (JobEscrow.State s) {
        (,,,,, s,) = escrow.jobs(id);
    }

    function _fundAssign() internal {
        vm.prank(coder);
        escrow.fund{value: PAY}(JOB, verifier, deadline);
        vm.prank(coordinator);
        escrow.assign(JOB, provider);
    }

    function _submitted() internal {
        _fundAssign();
        vm.prank(provider);
        escrow.submitResult(JOB, RESULT);
    }

    function test_honest_path_pays_provider_once() public {
        _submitted();
        vm.prank(verifier);
        escrow.verify(JOB, RESULT, true);
        vm.prank(coder);
        escrow.release(JOB);
        assertEq(provider.balance, PAY);
        assertEq(address(escrow).balance, 0);
        assertEq(uint8(_state(JOB)), uint8(JobEscrow.State.Released));

        vm.prank(coder);
        vm.expectRevert(abi.encodeWithSelector(JobEscrow.BadState.selector, JobEscrow.State.Released));
        escrow.release(JOB);
        assertEq(provider.balance, PAY);
    }

    function test_provider_cannot_release_even_after_verification() public {
        _submitted();
        vm.prank(verifier);
        escrow.verify(JOB, RESULT, true);
        vm.prank(provider);
        vm.expectRevert(JobEscrow.NotAllowed.selector);
        escrow.release(JOB);
    }

    function test_release_before_verification_reverts() public {
        _submitted();
        vm.prank(coder);
        vm.expectRevert(abi.encodeWithSelector(JobEscrow.BadState.selector, JobEscrow.State.Submitted));
        escrow.release(JOB);
    }

    function test_rejected_result_never_pays_and_refunds_coder() public {
        _submitted();
        vm.prank(verifier);
        escrow.verify(JOB, RESULT, false);
        vm.prank(coder);
        vm.expectRevert(abi.encodeWithSelector(JobEscrow.BadState.selector, JobEscrow.State.Rejected));
        escrow.release(JOB);
        uint256 before = coder.balance;
        vm.prank(coder);
        escrow.refund(JOB);
        assertEq(coder.balance, before + PAY);
        assertEq(provider.balance, 0);
    }

    function test_verdict_must_name_the_submitted_hash() public {
        _submitted();
        vm.prank(verifier);
        vm.expectRevert(JobEscrow.HashMismatch.selector);
        escrow.verify(JOB, keccak256("other"), true);
    }

    function test_only_verifier_can_verify() public {
        _submitted();
        address[3] memory others = [provider, coder, coordinator];
        for (uint256 i = 0; i < others.length; i++) {
            vm.prank(others[i]);
            vm.expectRevert(JobEscrow.NotAllowed.selector);
            escrow.verify(JOB, RESULT, true);
        }
    }

    function test_only_assigned_provider_can_submit() public {
        _fundAssign();
        vm.prank(stranger);
        vm.expectRevert(JobEscrow.NotAllowed.selector);
        escrow.submitResult(JOB, RESULT);
    }

    function test_only_coordinator_assigns_and_roles_must_differ() public {
        vm.prank(coder);
        escrow.fund{value: PAY}(JOB, verifier, deadline);
        vm.prank(stranger);
        vm.expectRevert(JobEscrow.NotAllowed.selector);
        escrow.assign(JOB, provider);
        vm.startPrank(coordinator);
        vm.expectRevert(JobEscrow.InvalidArgument.selector);
        escrow.assign(JOB, verifier);
        vm.expectRevert(JobEscrow.InvalidArgument.selector);
        escrow.assign(JOB, coder);
        vm.stopPrank();
    }

    function test_coder_cannot_be_own_verifier_and_job_ids_are_single_use() public {
        vm.startPrank(coder);
        vm.expectRevert(JobEscrow.InvalidArgument.selector);
        escrow.fund{value: PAY}(JOB, coder, deadline);
        escrow.fund{value: PAY}(JOB, verifier, deadline);
        vm.expectRevert(abi.encodeWithSelector(JobEscrow.BadState.selector, JobEscrow.State.Funded));
        escrow.fund{value: PAY}(JOB, verifier, deadline);
        vm.stopPrank();
    }

    function test_submission_after_deadline_reverts_and_coder_can_refund() public {
        _fundAssign();
        vm.warp(deadline);
        vm.prank(provider);
        vm.expectRevert(JobEscrow.TooLate.selector);
        escrow.submitResult(JOB, RESULT);
        vm.prank(coder);
        escrow.refund(JOB);
        assertEq(uint8(_state(JOB)), uint8(JobEscrow.State.Refunded));
    }

    function test_refund_of_assigned_job_waits_for_deadline() public {
        _fundAssign();
        vm.prank(coder);
        vm.expectRevert(JobEscrow.TooEarly.selector);
        escrow.refund(JOB);
    }

    function test_silent_verifier_refund_only_after_window() public {
        _submitted();
        vm.warp(uint256(deadline) + WINDOW - 1);
        vm.prank(coder);
        vm.expectRevert(JobEscrow.TooEarly.selector);
        escrow.refund(JOB);
        vm.warp(uint256(deadline) + WINDOW);
        vm.prank(coder);
        escrow.refund(JOB);
    }

    function test_cannot_refund_after_verified_or_released() public {
        _submitted();
        vm.prank(verifier);
        escrow.verify(JOB, RESULT, true);
        vm.warp(uint256(deadline) + WINDOW + 1);
        vm.prank(coder);
        vm.expectRevert(abi.encodeWithSelector(JobEscrow.BadState.selector, JobEscrow.State.Verified));
        escrow.refund(JOB);
    }

    function test_reassign_before_submission_only() public {
        _fundAssign();
        address backup = makeAddr("backup");
        vm.prank(coordinator);
        escrow.assign(JOB, backup);
        vm.prank(provider);
        vm.expectRevert(JobEscrow.NotAllowed.selector);
        escrow.submitResult(JOB, RESULT);
        vm.prank(backup);
        escrow.submitResult(JOB, RESULT);
        vm.prank(coordinator);
        vm.expectRevert(abi.encodeWithSelector(JobEscrow.BadState.selector, JobEscrow.State.Submitted));
        escrow.assign(JOB, provider);
    }

    function test_reentrant_provider_is_paid_exactly_once() public {
        ReentrantProvider bad = new ReentrantProvider(escrow);
        bad.setJob(JOB);
        vm.prank(coder);
        escrow.fund{value: PAY}(JOB, verifier, deadline);
        vm.deal(address(escrow), address(escrow).balance + 5 ether); // other jobs' funds sit here too
        vm.prank(coordinator);
        escrow.assign(JOB, address(bad));
        bad.submit(RESULT);
        vm.prank(verifier);
        escrow.verify(JOB, RESULT, true);
        vm.prank(verifier);
        escrow.release(JOB);
        assertTrue(bad.reentered());
        assertEq(address(bad).balance, PAY);
    }

    function test_reentrant_coder_is_refunded_exactly_once() public {
        ReentrantCoder bad = new ReentrantCoder(escrow);
        vm.deal(address(bad), 0);
        vm.deal(address(this), PAY);
        bad.fund{value: PAY}(JOB, verifier, deadline);
        vm.deal(address(escrow), address(escrow).balance + 5 ether); // other jobs' funds
        bad.refund();
        assertEq(address(bad).balance, PAY);
        assertEq(address(escrow).balance, 5 ether);
    }

    function testFuzz_no_one_but_coder_or_verifier_releases(address caller) public {
        vm.assume(caller != coder && caller != verifier);
        _submitted();
        vm.prank(verifier);
        escrow.verify(JOB, RESULT, true);
        vm.prank(caller);
        vm.expectRevert(JobEscrow.NotAllowed.selector);
        escrow.release(JOB);
    }
}
