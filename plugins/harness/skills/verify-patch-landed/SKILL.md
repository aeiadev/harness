---
name: verify-patch-landed
description: Verify files after a scripted edit, codemod, patch helper, or multi-file rewrite. Load before calling an indirect edit done; a command can succeed with a partial change.
---

# Verify patch landed

An edit command's exit code does not prove that the intended change reached
every file. Check the files on disk before reporting completion.

1. List the intended files and the observable change expected in each one.
2. Read each affected region from disk. Search for the new content and, when
   relevant, confirm that obsolete content is absent. Use literal searches such
   as `rg -n -F -- 'expected text' path/to/file`; do not treat a failed search as
   successful verification.
3. Inspect `git diff --check`, `git diff --stat`, and the relevant file diffs.
   Also inspect newly created, untracked files, which ordinary diffs omit.
4. Run the narrow check that proves the resulting behavior. A text match alone
   does not establish that code parses or behaves correctly.
5. Compare the observed files with the intended list. If any change is missing,
   fix it and repeat the checks for the affected files.

When an exact artifact is expected, compute its SHA-256 with Python's standard
`hashlib` module and compare it with the expected digest. Do not invent a digest
after the edit and call that an independent check.

Never infer success from the transformation script's own summary, verify only
one file from a multi-file edit, or overwrite unrelated work to simplify the
diff. Treat a Stop hook warning as a reminder to gather this evidence.

Return: files checked, expected changes found, check commands and results, and
any missing change. Claim completion only when the observed result matches the
requested change.
