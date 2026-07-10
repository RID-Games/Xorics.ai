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

# ---- Android/Kotlin verify layer (sandbox-compiler brick, 2026-07-09) ----------
# WHY: the python suite is structurally blind to Kotlin — a 484-line hallucinated
# rewrite of ChatActivity.kt sailed through "full suite PASSED" today and only a
# human eyeball caught it. With the compiler in the verify, drift fails loudly and
# the coder reads real kotlinc errors and fixes them in-loop (draft-then-fix).
#
# DESIGN: the runtime sandbox stays network=False (hermetic). Everything a compile
# needs is therefore baked HERE, where the image build legitimately has network:
#   * JDK 21            — AGP 8.7.x's requirement
#   * Gradle 8.10.2     — standalone, matching the repo wrapper version, because
#                         the selfedit workspace excludes *.jar so ./gradlew can
#                         never run there; `gradle` on PATH is the runtime entry.
#   * cmdline-tools     — licenses pre-accepted so AGP may auto-fetch SDK pieces
#                         during the warm build below.
#   * a WARM COMPILE of this repo's real android/ tree — whatever gradle resolves
#     (AGP, Kotlin compiler, deps, platform 35, build-tools) lands in /root/.gradle
#     and $ANDROID_HOME, so the runtime verify runs `gradle --offline`.
# Rebuild this image whenever android dependencies change (a new implementation()
# in build.gradle.kts will fail --offline with a clear "offline mode" error — that
# is the signal to rebuild, not a bug).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jdk-headless curl unzip \
    && rm -rf /var/lib/apt/lists/*

ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk
ENV PATH=$PATH:/opt/gradle/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools

RUN curl -fsSL https://services.gradle.org/distributions/gradle-8.10.2-bin.zip -o /tmp/g.zip \
    && unzip -q /tmp/g.zip -d /opt && mv /opt/gradle-8.10.2 /opt/gradle && rm /tmp/g.zip

RUN mkdir -p $ANDROID_HOME/cmdline-tools \
    && curl -fsSL https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -o /tmp/ct.zip \
    && unzip -q /tmp/ct.zip -d $ANDROID_HOME/cmdline-tools \
    && mv $ANDROID_HOME/cmdline-tools/cmdline-tools $ANDROID_HOME/cmdline-tools/latest \
    && rm /tmp/ct.zip \
    && yes | sdkmanager --licenses >/dev/null

# The warm compile. .containerignore keeps android/app/build and android/.gradle
# out of the build context, so this copies SOURCES, not gigabytes of host output.
COPY android /tmp/warm/android
RUN cd /tmp/warm/android && gradle --no-daemon :app:compileDebugKotlin \
    && rm -rf /tmp/warm

# The runner overrides cwd via the mount; /work just keeps an interactive
# `podman run -it localhost/xorics-sandbox` intuitive. No CMD: sandbox.run()
# always passes the command explicitly, so any CMD here would be ignored.
# sh -lc is a LOGIN shell: /etc/profile resets PATH, erasing image ENV PATH additions.
# Park gradle where the default PATH survives.
RUN ln -s /opt/gradle/bin/gradle /usr/local/bin/gradle

WORKDIR /work
