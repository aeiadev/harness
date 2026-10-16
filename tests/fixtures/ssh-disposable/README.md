# Disposable SSH proof fixture

This fixture builds the immutable Ubuntu base and exact OpenSSH, rsync and
Python package versions recorded in `fixture-lock.json`. Build requires the
pinned base image to be present locally and package-repository access; it fails
if the selected versions are unavailable. There is no fallback to latest.

From the repository root:

```sh
podman pull docker.io/library/ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
python3 -B tests/fixtures/ssh-disposable/run.py --build
python3 -B tests/fixtures/ssh-disposable/run.py
SSH_FIXTURE_REQUIRED=1 python3 -B tests/test_ssh_observation.py
```

Podman is preferred; `--engine docker` selects Docker explicitly. The container
has no host mounts or published ports. Its network namespace has loopback only.
No host SSH agent, configuration, known-hosts file or private key is read. New
fixture-only worker, observer and host keys are generated inside the container
for each run. The server accepts keys only and listens on container loopback.
The client pins the generated host key and uses strict host-key checking.

The worker uploads one artifact by modern SFTP-backed SCP, renames it, and
uploads a directory through rsync's default SSH transport. The separate
observer account reads the actual resulting bytes and is denied a directory
creation by server-side filesystem permissions. The target bytes and absence
of the rejected directory are checked afterward. Rsync archive mode includes
symlink and metadata behavior: the proof uses known synthetic content and does
not establish arbitrary source-content safety.

The JSON smoke report records expected/observed digests, the denied-write exit,
mutation count and actual client/image versions. `guard_qualified: false`
deliberately distinguishes this server/credential smoke from the separate
Harness guard and Router observation integration tests. It does not qualify
production servers, live MCP access or installed Claude/Codex hooks.

The exact named container is removed on success or handled failure. A bounded
600-second lifetime and engine `--rm` also stop and remove it if the parent is
forcibly interrupted; credentials can remain in that container until its lease
ends. No key is baked into the image. Retained images contain tooling only.
A failed explicit cleanup raises an error.

The Linux `ssh-integration` CI job prepares the pinned image, then runs the
mandatory test invocation above. Image/package preparation uses repository
network access; the running fixture has no external network access. The ordinary
portable test suite explicitly records the real SSH tests as unrun unless
`SSH_FIXTURE_RUN=1` or `SSH_FIXTURE_REQUIRED=1` is set. Once enabled, a missing
engine/image, startup error or missing guard implementation fails the tests;
none silently falls back to a simulation. macOS/Windows and live provider
qualification require their own evidence.
