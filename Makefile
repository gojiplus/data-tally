PYTHON ?= python
DOCKER_IMAGE ?= python:3.14-slim

.PHONY: ci lint test build format tally refresh ci-docker
ci: lint test build
lint:
	$(PYTHON) -m black --check src tests
	$(PYTHON) -m isort --check-only src tests
	$(PYTHON) -m flake8 src tests
format:
	$(PYTHON) -m black src tests
	$(PYTHON) -m isort src tests
test:
	$(PYTHON) -m pytest -q
build:
	$(PYTHON) -m build
tally:
	$(PYTHON) -m data_tally.cli datasets.toml
refresh:
	$(PYTHON) -m data_tally.refresh
ci-docker:
	docker run --rm -v "$(CURDIR):/workspace:ro" $(DOCKER_IMAGE) sh -c 'cp -a /workspace /tmp/project && cd /tmp/project && python -m pip install -e ".[dev]" && python -m black --check src tests && python -m isort --check-only src tests && python -m flake8 src tests && python -m pytest -q && python -m build'
