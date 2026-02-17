# Workflow Conductor — Development Interface
# =============================================

.DEFAULT_GOAL := help
SHELL := /bin/bash

# Python
UV := uv
SRC := src/workflow_conductor
TESTS := tests

# K8s — Kind (local dev)
CLUSTER_NAME ?= hyperflow-test
KIND_CONFIG := local/kind-config-1n.yaml
K8S_DEPLOYMENT_PATH ?= ../../hyperflow-k8s-deployment

# K8s — k3s (VM)
K3S_VERSION := $(shell grep 'k3s_version:' local/k3s/version.yaml 2>/dev/null | cut -d' ' -f2)
K3S_KUBECONFIG := /etc/rancher/k3s/k3s.yaml

# --- Help ---
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

# --- Setup ---
.PHONY: install
install: ## Install project with dev dependencies
	$(UV) sync --all-extras

.PHONY: install-pre-commit
install-pre-commit: ## Install pre-commit hooks
	$(UV) run pre-commit install

# --- Code Quality ---
.PHONY: lint
lint: ## Run linters (ruff)
	$(UV) run ruff check $(SRC) $(TESTS)
	$(UV) run ruff format --check $(SRC) $(TESTS)

.PHONY: format
format: ## Auto-format code
	$(UV) run ruff format $(SRC) $(TESTS)
	$(UV) run ruff check --fix $(SRC) $(TESTS)

.PHONY: typecheck
typecheck: ## Run type checker (mypy)
	$(UV) run mypy $(SRC)

# --- Testing ---
.PHONY: test
test: ## Run unit tests
	$(UV) run pytest $(TESTS)/unit -v --durations=10

.PHONY: test-integration
test-integration: cluster-ready ## Run integration tests (requires Kind cluster)
	$(UV) run pytest $(TESTS)/integration -v --timeout=300

.PHONY: test-e2e
test-e2e: cluster-ready ## Run E2E tests (requires Kind cluster + images)
	$(UV) run pytest $(TESTS)/e2e -v --timeout=600

.PHONY: test-all
test-all: test test-integration test-e2e ## Run all tests

.PHONY: coverage
coverage: ## Run tests with coverage report
	$(UV) run pytest $(TESTS)/unit --cov=$(SRC) --cov-report=term-missing --cov-report=html

# --- CLI ---
.PHONY: run
run: ## Run conductor with example prompt
	$(UV) run workflow-conductor run \
		"Analyze frequency of genetic variants across European and East Asian populations for chromosomes 1 through 5. Use moderate parallelism."

.PHONY: run-dry
run-dry: ## Run conductor in dry-run mode (no K8s deployment)
	$(UV) run workflow-conductor run --dry-run \
		"Analyze frequency of genetic variants across European and East Asian populations for chromosomes 1 through 5."

.PHONY: run-query
run-query: ## Run with custom prompt (usage: make run-query Q="...")
	$(UV) run workflow-conductor run "$(Q)"

.PHONY: demo
demo: ## Run conductor in interactive demo mode (explanations + pauses)
	$(UV) run workflow-conductor run --demo --auto-approve \
		"Analyze BRCA1 gene variants in the British population"

.PHONY: demo-test
demo-test: ## Run conductor in non-interactive demo mode (no pauses)
	$(UV) run workflow-conductor run --demo --auto-approve --no-pause \
		"Analyze BRCA1 gene variants in the British population"

.PHONY: run-debug
run-debug: ## Run conductor in debug mode
	HF_CONDUCTOR_LOG_LEVEL=DEBUG $(UV) run workflow-conductor run \
		"Analyze EUR population, chromosome 22, small parallelism."

# --- K8s Cluster ---
.PHONY: cluster-create
cluster-create: ## Create Kind cluster for testing
	@if kind get clusters 2>/dev/null | grep -q "^$(CLUSTER_NAME)$$"; then \
		echo "Cluster '$(CLUSTER_NAME)' already exists"; \
	else \
		kind create cluster --name $(CLUSTER_NAME) --config $(KIND_CONFIG); \
	fi
	kubectl config use-context kind-$(CLUSTER_NAME)

.PHONY: cluster-delete
cluster-delete: ## Delete Kind cluster
	kind delete cluster --name $(CLUSTER_NAME)

.PHONY: cluster-ready
cluster-ready: ## Verify cluster is available
	@kubectl cluster-info > /dev/null 2>&1 || \
		(echo "ERROR: No cluster. Run 'make cluster-create' first." && exit 1)
	@kubectl wait --for=condition=Ready nodes --all --timeout=60s > /dev/null

.PHONY: cluster-status
cluster-status: ## Show cluster and pod status
	@echo "=== Cluster ==="
	kind get clusters
	@echo ""
	@echo "=== Nodes ==="
	kubectl get nodes -o wide 2>/dev/null || true
	@echo ""
	@echo "=== Pods ==="
	kubectl get pods -A 2>/dev/null || true
	@echo ""
	@echo "=== Helm Releases ==="
	helm list 2>/dev/null || true

.PHONY: cluster-load-images
cluster-load-images: cluster-ready ## Load Docker images into Kind cluster (skips if present)
	@for img in hyperflowwms/hyperflow:latest \
	            hyperflowwms/1000genome-worker:1.0-je1.3.4 \
	            hyperflowwms/1000genome-data:1.0 \
	            broadinstitute/gatk:4.4.0.0; do \
		if docker exec $(CLUSTER_NAME)-worker crictl images --no-trunc 2>/dev/null \
		   | grep -q "$$(echo $$img | cut -d: -f1)"; then \
			echo "Already loaded: $$img"; \
		else \
			echo "Loading: $$img"; \
			kind load docker-image $$img --name $(CLUSTER_NAME) 2>/dev/null || true; \
		fi; \
	done

.PHONY: docker-pull-images
docker-pull-images: ## Pull all required Docker images
	docker pull hyperflowwms/hyperflow:latest
	docker pull hyperflowwms/1000genome-worker:1.0-je1.3.4
	docker pull hyperflowwms/1000genome-data:1.0
	docker pull hyperflowwms/1000genome-mcp:2.3
	docker pull broadinstitute/gatk:4.4.0.0

# --- k3s Cluster (VM) ---
.PHONY: k3s-install
k3s-install: ## Install k3s (config-driven, persistent storage)
	@test -n "$(K3S_VERSION)" || (echo "ERROR: K3S_VERSION not set. Check local/k3s/version.yaml" && exit 1)
	@dpkg -s nfs-common >/dev/null 2>&1 || \
		(echo "Installing nfs-common (required for NFS volume mounts)..." && \
		 sudo apt-get update -qq && sudo apt-get install -y -qq nfs-common)
	sudo mkdir -p /etc/rancher/k3s
	sudo cp local/k3s/config.yaml /etc/rancher/k3s/config.yaml
	curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$(K3S_VERSION)" sh -
	@echo "Waiting for node to be Ready..."
	@KUBECONFIG=$(K3S_KUBECONFIG) kubectl wait --for=condition=Ready nodes --all --timeout=120s

.PHONY: k3s-pull-images
k3s-pull-images: ## Pre-pull images into k3s containerd
	@for img in hyperflowwms/hyperflow:latest \
	            hyperflowwms/1000genome-worker:1.0-je1.3.4 \
	            hyperflowwms/1000genome-data:1.0 \
	            hyperflowwms/1000genome-mcp:2.3 \
	            broadinstitute/gatk:4.4.0.0; do \
		if sudo k3s crictl images --no-trunc 2>/dev/null | grep -q "$$(echo $$img | cut -d: -f1)"; then \
			echo "Already present: $$img"; \
		else \
			echo "Pulling: $$img"; \
			sudo k3s crictl pull "docker.io/$$img"; \
		fi; \
	done

.PHONY: k3s-setup
k3s-setup: k3s-install k3s-pull-images ## Full k3s setup: install + pull images

.PHONY: k3s-status
k3s-status: ## Show k3s cluster status
	@echo "=== k3s Version ==="
	k3s --version 2>/dev/null || echo "k3s not installed"
	@echo ""
	@echo "=== Nodes ==="
	KUBECONFIG=$(K3S_KUBECONFIG) kubectl get nodes -o wide 2>/dev/null || true
	@echo ""
	@echo "=== Pods ==="
	KUBECONFIG=$(K3S_KUBECONFIG) kubectl get pods -A 2>/dev/null || true
	@echo ""
	@echo "=== Images ==="
	sudo k3s crictl images 2>/dev/null || true

.PHONY: k3s-reset
k3s-reset: ## Uninstall k3s and remove data (interactive)
	@echo -n "This will remove k3s and all data in /media/persistence/k3s. Continue? [y/N] " && \
		read answer && if [ "$${answer:-N}" = y ]; then \
			echo "Uninstalling k3s..." ; \
			/usr/local/bin/k3s-uninstall.sh ; \
			sudo rm -rf /media/persistence/k3s ; \
		else \
			echo "Aborted." ; \
		fi

# --- Infrastructure ---
.PHONY: infra-up
infra-up: cluster-create ## Deploy hf-ops infrastructure
	@test -d "$(K8S_DEPLOYMENT_PATH)" || \
		(echo "ERROR: K8S_DEPLOYMENT_PATH='$(K8S_DEPLOYMENT_PATH)' not found. Set it to the hyperflow-k8s-deployment repo." && exit 1)
	helm upgrade --install hf-ops $(K8S_DEPLOYMENT_PATH)/charts/hyperflow-ops \
		--dependency-update --wait --timeout 15m \
		-f $(K8S_DEPLOYMENT_PATH)/local/values-fast-test-ops.yaml

.PHONY: infra-down
infra-down: ## Tear down hf-ops and hf-run
	-helm delete hf-run 2>/dev/null
	-helm delete hf-ops 2>/dev/null

.PHONY: infra-status
infra-status: ## Check infrastructure component status
	@echo "=== Helm Releases ==="
	helm list
	@echo ""
	@echo "=== Infrastructure Pods ==="
	kubectl get pods -l 'app in (nfs-server-provisioner,redis)' -o wide 2>/dev/null || true

# --- Full Setup / Teardown ---
.PHONY: setup
setup: install docker-pull-images cluster-create cluster-load-images infra-up ## Full setup from scratch
	@echo "Setup complete. Run 'make run' to start the conductor."

.PHONY: teardown
teardown: infra-down ## Remove Helm releases (keep cluster)

.PHONY: teardown-all
teardown-all: teardown cluster-delete ## Full cleanup (cluster + releases)

# --- Utility ---
.PHONY: logs
logs: ## Tail HyperFlow engine logs
	@POD=$$(kubectl get pods -l component=hyperflow-engine \
		-o jsonpath='{.items[0].metadata.name}' 2>/dev/null); \
	if [ -n "$$POD" ]; then \
		kubectl logs -f "$$POD" -c hyperflow; \
	else \
		echo "No engine pod found"; \
	fi

.PHONY: clean
clean: ## Remove build artifacts
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache .mypy_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

.PHONY: clean-all
clean-all: clean cluster-delete ## Clean everything including cluster

# --- CI ---
.PHONY: ci
ci: lint typecheck test ## Run all CI checks
