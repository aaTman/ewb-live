setup:
    uv sync
test:
    uv run pytest -q -m "not network and not e2e"
e2e:
    uv run pytest -m e2e -q
lint:
    uvx ruff check .
run:
    # NOTE: ops/remote.sh run SYNCS ITS OWN WORKTREE to the box before running;
    # invoke only from a worktree exactly at the deployed SHA (see AGENTS.md).
    ./ops/remote.sh run python -m ewb_live.pipeline --days 90
weekly:
    ./ops/remote.sh bg weekly python -m ewb_live.weekly
deploy:
    @echo "No shortcut deploy. Follow the AGENTS.md Deploy SOP: merged SHA worktree -> rsync code dirs -> md5-manifest verify -> restart web -> log DEPLOYED_FROM. Never while the weekly runs."
