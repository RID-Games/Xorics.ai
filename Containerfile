# Xorics — sandbox runner image. python:3.12-slim + ONLY the deps the hermetic
# suite imports. The repo CODE is intentionally NOT baked in: sandbox.run() copies
# the working tree and bind-mounts that copy at /work (`-w /work`), which shadows
# anything an image might carry. So this image holds the Python ENV and nothing
# else — enough that ./run_tests.sh (which finds no venv/ in the copy and falls
# back to `python3`) can `import xorics` and run every suite, hermetically.
#
# Build, then point the runner at it:
#   podman build -t localhost/xorics-sandbox:latest -f Containerfile .
#   export XORICS_SANDBOX_IMAGE=localhost/xorics-sandbox:latest
#
# XORICS-FEATURE: sandbox
FROM docker.io/library/python:3.12-slim

# Quiet, cache-free, no stray .pyc layers.
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# All five ship cp312 manylinux wheels (numpy, PyMuPDF, and the Rust-backed
# pydantic-core / primp included), so no compiler is needed on slim. If a future
# version ever drops its wheel, uncomment the build-tools line below:
# RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements-sandbox.txt /tmp/requirements-sandbox.txt
RUN pip install -r /tmp/requirements-sandbox.txt && rm -f /tmp/requirements-sandbox.txt

# Fail the BUILD (not some later mysterious run) if the dep surface can't import.
# fitz is PyMuPDF's import name; ddgs is the current duckduckgo package.
RUN python -c "import openai, numpy, fitz, ddgs, fastapi; print('xorics sandbox deps OK')"

# The runner overrides cwd via the mount; /work just keeps an interactive
# `podman run -it localhost/xorics-sandbox` intuitive. No CMD: sandbox.run()
# always passes the command explicitly, so any CMD here would be ignored.
WORKDIR /work
