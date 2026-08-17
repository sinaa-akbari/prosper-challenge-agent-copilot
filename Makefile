# Prosper Challenge — run everything from the repo root.
# Python deps are managed with uv (https://docs.astral.sh/uv/), the UI with npm.

PROJECT := backend
UI      := frontend

.PHONY: help install seed build dev run ui clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install Python and UI dependencies
	uv sync --directory $(PROJECT)
	cd $(UI) && npm install

seed: ## Create the demo agent and its mock production calls
	uv run --directory $(PROJECT) python seed.py

reseed: ## Wipe the demo agent back to v1 (clears versions, tests, runs, issues)
	uv run --directory $(PROJECT) python seed.py --force

build: ## Build the UI into frontend/dist (served by the Python app)
	cd $(UI) && npm run build

cert: ## Generate a self-signed cert so `make run` serves https (mic needs it on the LAN)
	uv run --directory $(PROJECT) python make_cert.py

run: ## Serve the app on localhost:7860 — https if `make cert` was run, else http (needs `make build` first)
	uv run --directory $(PROJECT) python server.py

dev: ## UI dev server on :5173 with hot reload — run `make run` alongside it
	cd $(UI) && npm run dev

smoke: ## Exercise the simulator, Copilot, and call analyser from the CLI
	uv run --directory $(PROJECT) python smoke.py

clean: ## Remove the venv, build output, and Python caches
	rm -rf $(PROJECT)/.venv $(UI)/dist $(UI)/node_modules
	find $(PROJECT) -type d -name __pycache__ -prune -exec rm -rf {} +
