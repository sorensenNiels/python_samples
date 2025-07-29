export PYTHON_VERSION=3.13.3

# Install python dependencies
install:
	uv sync

# Install pre-commit hooks
pre_commit_setup:
	uv run pre-commit install

# Install python dependencies and pre-commit hooks
setup: install pre_commit_setup

# Run pre-commit
pre_commit:
	uv run pre-commit run -a

# Update dependencies
update:
	uv lock --upgrade
	uv sync
	rm -rf ./.cache
	mkdir -p ./.cache
	${MAKE} lint


# Run pytest
test:
	uv run pytest

# Run formatter
format: 
	uv run ruff check --select I --fix
	uv run ruff format

# Run linter
lint:
	uv run ruff check
	uv run mypy .

run-sample:
	uv run --directory=apps/sample fastapi dev sample/server.py

run-simple-storage:
	uv run --directory=apps/simple_storage fastapi dev simple_storage/main.py

run-simple-worker:
	uv run --directory=apps/simple_worker fastapi dev simple_worker/main.py


# MONGO
# Connection string inside dev container: mongodb://root:secret@172.17.0.1:30001/?ssl=false&readPreference=primary
mongo-clean:
	-@docker stop local-mongo
	-@docker rm local-mongo
	-@docker run -e MONGO_INITDB_ROOT_USERNAME=root -e MONGO_INITDB_ROOT_PASSWORD=secret -d --name local-mongo -p 30001:27017 mongo
	-@docker start local-mongo

mongo:
	-@docker stop local-mongo
	-@docker start local-mongo	


