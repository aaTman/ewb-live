setup:
    uv sync
test:
    uv run pytest -q
e2e:
    uv run pytest -m e2e -q
lint:
    uvx ruff check .
run:
    ./ops/remote.sh run python -m ewb_live.pipeline --days 90
weekly:
    ./ops/remote.sh bg weekly python -m ewb_live.weekly
deploy:
    echo "TODO: deploy via CI (Step 6); services are installed by ops/install.sh on the ewb LXC"
