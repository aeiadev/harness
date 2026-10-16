# Contributing

Run the whole suite with `python3 tests/run_all.py` (`python tests/run_all.py` in Git Bash on Windows); it runs every test file and lists every failure.

No test calls a model or the network; CI also checks out the sibling repository and builds the old release trees.

The exception is opt-in: with `SSH_FIXTURE_REQUIRED=1` the real SSH tests start a local container, and preparing its pinned image needs a container engine and network access (see `tests/fixtures/ssh-disposable/README.md`). The default run skips them and records them as unrun.

Python 3.9 or newer, standard library only.

Keep a change small and add a test that fails without it.

Run one test file with `python3 tests/test_x.py`.
The upgrade and together tests need ROUTER_V01, ROUTER_V02, HARNESS_V01 and HARNESS_V02 set to trees of the published 0.1 and 0.2 commits; the Old release trees step in .github/workflows/ci.yml shows how.
The release step edits README.md and CHANGELOG.md.

## Releasing

Router and Harness release together at the same version X.Y.Z, the same day, Router first.

1. Set VERSION to X.Y.Z and start CHANGELOG.md with `## X.Y.Z`; its first line starts with `Pairs with Router X.Y.Z.`
2. Check the release text with `scripts/release-notes.sh X.Y.Z`.
3. Commit, then push Router main and then Harness main, before any tag:

   ```bash
   git push origin main
   ```

4. Tag each repository, Router first, and push the tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

5. Publish the GitHub Release with:

   ```bash
   gh release create vX.Y.Z --title "Harness X.Y.Z" --notes "$(scripts/release-notes.sh X.Y.Z)"
   ```
