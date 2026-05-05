---
name: Build tooling — no buildx, native-arch per machine
description: User does not use docker buildx. Each machine builds for its own native arch with plain `docker build`. Do not propose buildx, TARGETARCH, or multi-arch manifests.
type: feedback
originSessionId: b11809d9-b944-4609-aaa7-3d39ed9996b3
---
Do not reference `docker buildx`, `buildx build`, `buildx version`, BuildKit-specific multi-arch
features, `${TARGETARCH}` / `${TARGETPLATFORM}` ARGs, or multi-platform manifests when discussing
the airgap-test-toolchain build process.

**Why:** User explicit instruction (2026-04-26) — *"buildx 안써. 앞으로도 언급하지마."* The build
script [build-mac.sh:9-12](../../../Developer/airgap-test-toolchain/code-AI-quality-allinone/scripts/build-mac.sh#L9-L12)
documents the rationale: *"buildx 의 export→load 오버헤드 (14GB tarball 직렬화, 수십 분 소요) 가
순수 낭비였다."* — buildx's export→load step serializes the 14GB image tarball, taking tens of
minutes vs. plain `docker build` which lands directly in the local daemon.

**How to apply:**

- **Build command** — plain `DOCKER_BUILDKIT=1 docker build -f Dockerfile -t <image> <context>`.
  No `buildx`, no `--platform`, no `--load`.
- **Per-machine native arch** — Apple Silicon (M4 Pro) builds linux/arm64; WSL2/Intel builds
  linux/amd64. Two separate machines, two builds, two tarballs. No multi-arch manifests.
- **Dockerfile arch handling** — do NOT use `${TARGETARCH}`. Use one of:
  (a) glob with single match: `COPY offline-assets/meilisearch/meilisearch-linux-* /usr/local/bin/meilisearch`
      — each machine pre-downloads only its own arch binary, glob resolves to one file.
  (b) `RUN ARCH=$(dpkg --print-architecture) && cp ...` — runtime detection inside RUN.
- **`offline-assets/<binary>/`** — each machine downloads only the binary matching its native
  arch (not both arch). Saves disk, simplifies Dockerfile glob.
- **Existing scripts** — [scripts/offline-prefetch.sh:74-80](../../../Developer/airgap-test-toolchain/code-AI-quality-allinone/scripts/offline-prefetch.sh#L74-L80)
  may still contain `docker buildx build` from earlier era — do NOT touch unless user asks.
  Future docs and new scripts use plain `docker build` only.
