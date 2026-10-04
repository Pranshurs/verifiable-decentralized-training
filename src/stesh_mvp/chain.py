"""Local EVM: start Anvil, deploy JobEscrow, and send transactions signed by each party's key.

Each role (coder, provider, verifier, coordinator) signs its own transactions locally, as
it would on a real network. Nothing relies on the node's unlocked accounts. Funds are
test ETH on a throwaway chain; there is no token.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from eth_account import Account
from web3 import Web3

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "contracts" / "out" / "JobEscrow.sol" / "JobEscrow.json"
# Anvil's well-known development mnemonic: public test keys, never use them for real funds.
DEV_MNEMONIC = "test test test test test test test test test test test junk"
STATES = ["None", "Funded", "Assigned", "Submitted", "Verified", "Released", "Rejected", "Refunded"]


def dev_keys(n: int = 10) -> list[str]:
    Account.enable_unaudited_hdwallet_features()
    return ["0x" + Account.from_mnemonic(DEV_MNEMONIC, account_path=f"m/44'/60'/0'/0/{i}").key.hex().removeprefix("0x")
            for i in range(n)]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Anvil:
    def __init__(self) -> None:
        self.port = _free_port()
        self.proc = subprocess.Popen(["anvil", "--port", str(self.port), "--silent", "--chain-id", "31337"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.url = f"http://127.0.0.1:{self.port}"
        w3 = Web3(Web3.HTTPProvider(self.url))
        for _ in range(100):
            try:
                if w3.is_connected():
                    return
            except Exception:
                pass
            time.sleep(0.05)
        self.stop()
        raise RuntimeError("anvil did not start")

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)

    def __enter__(self) -> "Anvil":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


class TxFailed(RuntimeError):
    pass


@dataclass
class TxResult:
    hash: str
    gas_used: int
    seconds: float


class Escrow:
    def __init__(self, w3: Web3, address: str):
        abi = json.loads(ARTIFACT.read_text())["abi"]
        self.w3, self.address = w3, address
        self.c = w3.eth.contract(address=address, abi=abi)

    @classmethod
    def deploy(cls, url: str, deployer_key: str, coordinator: str, verify_window_s: int = 3600) -> "Escrow":
        if not ARTIFACT.exists():
            raise FileNotFoundError("contracts not built: run `forge build` in contracts/")
        art = json.loads(ARTIFACT.read_text())
        w3 = Web3(Web3.HTTPProvider(url))
        factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]["object"])
        receipt = _send(w3, factory.constructor(coordinator, verify_window_s), deployer_key)[1]
        return cls(w3, receipt.contractAddress)

    def _tx(self, fn, key: str, value: int = 0) -> TxResult:
        t0 = time.perf_counter()
        tx_hash, receipt = _send(self.w3, fn, key, value)
        return TxResult(tx_hash, receipt.gasUsed, time.perf_counter() - t0)

    # -- protocol calls, one per role ----------------------------------------------
    def fund(self, coder_key: str, job_id: str, verifier: str, deadline: int, amount_wei: int) -> TxResult:
        return self._tx(self.c.functions.fund(job_id, verifier, deadline), coder_key, amount_wei)

    def assign(self, coordinator_key: str, job_id: str, provider: str) -> TxResult:
        return self._tx(self.c.functions.assign(job_id, provider), coordinator_key)

    def submit_result(self, provider_key: str, job_id: str, result_hash: str) -> TxResult:
        return self._tx(self.c.functions.submitResult(job_id, result_hash), provider_key)

    def verify(self, verifier_key: str, job_id: str, result_hash: str, accepted: bool) -> TxResult:
        return self._tx(self.c.functions.verify(job_id, result_hash, accepted), verifier_key)

    def release(self, caller_key: str, job_id: str) -> TxResult:
        return self._tx(self.c.functions.release(job_id), caller_key)

    def refund(self, coder_key: str, job_id: str) -> TxResult:
        return self._tx(self.c.functions.refund(job_id), coder_key)

    def job(self, job_id: str) -> dict:
        coder, provider, verifier, amount, deadline, state, result = self.c.functions.jobs(job_id).call()
        return {"coder": coder, "provider": provider, "verifier": verifier, "amount": amount,
                "deadline": deadline, "state": STATES[state], "result_hash": "0x" + result.hex()}

    def balance(self, address: str) -> int:
        return self.w3.eth.get_balance(address)

    def now(self) -> int:
        return self.w3.eth.get_block("latest")["timestamp"]

    def advance(self, seconds: int) -> None:
        self.w3.provider.make_request("evm_increaseTime", [seconds])
        self.w3.provider.make_request("evm_mine", [])


def _send(w3: Web3, fn, key: str, value: int = 0):
    acct = Account.from_key(key)
    try:
        tx = fn.build_transaction({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address),
                                   "value": value, "chainId": w3.eth.chain_id})
    except Exception as exc:  # gas estimation runs the call, so a revert surfaces here
        raise TxFailed(str(exc)) from exc
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    if receipt.status != 1:
        raise TxFailed(f"transaction {tx_hash.hex()} reverted")
    return tx_hash.hex(), receipt
