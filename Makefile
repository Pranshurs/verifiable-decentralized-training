.PHONY: setup contracts image test contract-test demo bench

setup:
	pip install -e ".[dev]"

contracts:
	cd contracts && forge build

image:
	docker build -f workloads/logreg-sgd/Dockerfile -t stesh-mvp/logreg-sgd:1 .

contract-test:
	cd contracts && forge test

test: contracts image
	pytest -q

demo: contracts image
	stesh-mvp-demo

bench: contracts image
	python -m stesh_mvp.bench --runs 5 --out benchmarks/results.json
