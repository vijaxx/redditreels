# Contributing

## Setup

```
git clone https://github.com/vijaxx/redditreels.git
cd redditreels
pip install -r requirements-dev.txt
```

You'll also need `ffmpeg` on your `PATH` for anything that touches rendering (`pipeline/render.py`). The test suite doesn't need it, or any live credentials — copy `config/credentials.example.json` to `config/credentials.json` only if you're actually running the pipeline, not for running tests.

## Running tests

```
python -m pytest tests/ -v
```

CI runs this plus `python -m compileall -q .` on every push and PR. Note: a couple of modules (e.g. `pipeline/rewrite_story.py`) read `credentials.json` at import time — `tests/conftest.py` handles dropping in a placeholder automatically so tests don't need a real key.

## Making a change

1. Branch off `main`.
2. Pure logic (the ad-safety scrubber, subreddit selection, scoring/filtering functions) is the easiest to add coverage for and the most valuable to test — see `tests/` for the existing pattern.
3. Anything that drives a live Chrome session or calls a platform API (`platforms/`, most of `tools/`) can't be exercised in CI; those changes get reviewed by reading the diff, not by a green check.
4. Open a PR against `main`.
