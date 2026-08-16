# wiki-generator on Amazon Bedrock.
#
# Two things in here are load-bearing and easy to get wrong:
#
#  1. The container does NOT run as root. Claude Code refuses to bypass
#     permission prompts as root unless it is told it is sandboxed, and this
#     image runs the generator with `--permission-mode bypassPermissions`
#     because there is no terminal to answer a prompt.
#  2. No credentials are baked in. Bedrock access comes from the ECS task role
#     at call time; an image carrying a key is a key in every registry it
#     reaches.

FROM node:22-bookworm-slim

# git: the scanner honours .gitignore via `git ls-files`, and without it every
# node_modules in the repository ends up in the wiki.
# ca-certificates: required to reach the Bedrock endpoint at all.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned on purpose: the generator probes CLI behaviour (subagents, JSON
# schemas, provider selection) and a silent major bump is a silent behaviour
# change. Raise it deliberately, with a test run.
ARG CLAUDE_CODE_VERSION=2.1.228
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && npm cache clean --force

WORKDIR /app
COPY wiki_generator ./wiki_generator
COPY README.md LICENSE ./

# `node` (uid 1000) ships with the base image. Running as a normal user is what
# keeps `bypassPermissions` usable; it also means the mounted volume must be
# writable by uid 1000 — see the EFS access point in ecs-task-definition.json.
RUN mkdir -p /wikis && chown -R node:node /app /wikis
USER node

# Deliberately not setting CLAUDE_CODE_USE_BEDROCK here: --bedrock is the single
# source of truth. Setting it in the image too would let the container talk to
# Bedrock while the generator believes it is on the subscription — and skip the
# region check that exists to catch exactly that.
ENV PYTHONUNBUFFERED=1 \
    HOME=/home/node

# No ENTRYPOINT arguments: the task definition supplies them, so one image
# serves a single repository, a whole tree, or a verification pass.
ENTRYPOINT ["python3", "-m", "wiki_generator"]
CMD ["--help"]
