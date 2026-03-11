.PHONY: install test run-web run-daemon

install:
	pip install -r requirements.txt

test:
	pytest

run-web:
	uvicorn src.web.app:app --host 0.0.0.0 --port 8001 --reload

run-daemon:
	python scripts/run.py
