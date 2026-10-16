# Remote operation policy contract (0.5.0)

This is the reviewed 0.5.0 contract and its synthetic corpus. The parser
(`hooks/guard/ssh_operations.py`), the owner binding CLI and the offline
`ssh check` command implement it, and the full Harness shell guard consults it
only when an owner has bound a project policy. It is off by default.
`tests/fixtures/ssh-operations.json` is schema version 1: `policy` is the
sample project policy; each case has an exact `command`, synthetic `cwd`,
activation `mode`/`state`, expected parser or gate result, and S1/S2/S3
coverage. The corpus test validates the fixture; separate tests run the parser,
binding, command and guard integration against it. Case IDs and reason IDs are stable fixture labels. `ALLOW` means
only that a literal invocation matches a rule. It does not authenticate a host,
read a source file, prove remote effects, or override server permissions.

## Positive deployment shape

The example binds `site-stage` to `deploy@example.test` on port 22. It permits
an exact `cat /srv/site/VERSION` (including a postdeployment readback), exact
`mkdir -p /srv/site/releases/r1`, exact
`mv /srv/site/releases/r1 /srv/site/current`, one local file upload under `/srv/site/releases`, and one
local tree upload under that root. Upload does not authorize rename, and rename
does not authorize removal. A specific deny protects `/srv/site/current` from
`rm`; another denies rsync deletion under the release root. A real deployment
that needs deletion requires a separately reviewed, scoped rule decision.
These names, bytes, and `.test` hosts are synthetic. No credentials, keys,
private host names, or production artifact contents belong in policy or
results. An `observer` command in the corpus is a guard denial example; actual
server enforced observer write denial is shown only by the opt-in disposable fixture described in the operations guide.

Policy schema 1 contains exactly a `schema_version`, a nonempty map of destination labels mapped
to literal `host`, `user`, `port`, and a nonempty rules list. Destination labels
are lowercase hyphenated identifiers. This subset uses nonempty ASCII host
names/addresses (letters, digits, dots, colons, hyphens; leading alphanumeric)
and user names (letters, digits, underscore, dot, hyphen; leading alphanumeric
or underscore), never patterns. Ports are integers 1–65535, not booleans.
Schema versions are integer 1, not booleans; unknown fields are rejected. A rule has a unique lowercase
hyphenated ID, `allow|deny`, `ssh|scp|rsync`, destination label, plus one of:
exact `argv`; absolute `remote_root`; or finite `operation` and absolute
`remote_root`. Paths use component containment, not string prefix matching.
Exact argv rules are SSH-only; root-only rules describe SCP/rsync uploads.
Operation names are `read|create-dir|rename|remove` for SSH, `upload` for SCP,
and `upload|delete|remove` for rsync. Every SSH allow must use exact argv; SSH operation/root shapes are deny-only.
Rsync `remove` classifies source-removal transfers; its root scopes the remote
upload destination, not the local source being removed. Root paths are canonical,
nonempty component paths, with no trailing slash, dot components or expansion.
The sample also permits exact `test -f /srv/site/VERSION` and exact
`/usr/local/bin/site-status --brief`; the latter asserts invocation only.
Its overlapping `remove-current-allow` rule deliberately tests deny precedence:
`protect-current` still blocks the identical invocation.
No regex, glob, executable configuration, broad host exemption or secret field
is allowed. Invalid or duplicate JSON keys/rule IDs are refused. Explicit
recognized deny precedes allow; an unsupported/dynamic portion prevents allow.

## Literal input boundary

Only POSIX style literal input is in scope. The lexer must preserve source
spans, quoting and escaping and consume the entire input. It recognizes
command-position `ssh`, `scp`, `rsync`; literal `command`, `env`, `timeout`
wrappers; `;`, `&&`, `||` segments; and literal `sh -c` / `bash -c` wrappers
through depth 2. A wrapper or compound line is judged as a whole. A fully
scanned opaque local executable is outside SSH inspection coverage; an
incomplete scan is UNKNOWN even if SSH appears only beyond the limit. Arbitrary
programs that call SSH internally are outside interception coverage.

| Client | Admitted literal form | Explicitly unsupported visible form |
| --- | --- | --- |
| `ssh` | Destination with literal user/port (or declared defaults); `-p`, `-l`, `-i`, `-T`, `-n`; `-o` only for `BatchMode=yes`, `IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`, `UserKnownHostsFile=<literal>` | Conflicting user/port; interactive sessions, forwarding, tunnels, ProxyJump/ProxyCommand, unsupported options |
| `scp` | One literal local file to one remote absolute path, default SFTP profile; `-P`, `-i`, `-p` and the same reviewed `-o` values | `-O`, `-r`, remote-to-remote and ambiguous colon forms |
| `rsync` | One local directory ending `/` to `user@host:/absolute/path/`; `-a`/`--archive`, `--dry-run` | `--delete*`, `--remove-source-files`, `--relative`, `-e`, `--rsh`, `--rsync-path`, daemon syntax, dynamic transport |

Option boundaries are fixed before operand parsing. For `ssh`, `-p`, `-l`,
`-i`, and `-o` each consume exactly one value, either the next word or joined
to the option (`-p22`, `-ldeploy`, `-i/path`, `-oBatchMode=yes`). `-T` and
`-n` consume none and may combine as `-Tn`; value-taking options may end a
short-option group, consuming the remainder or the next word. For `scp`,
`-P`, `-i`, and `-o` consume one value by the same rule; `-p` consumes none
and may combine with those options. `-P` is the SCP port; lowercase `-p`
preserves file times and is not a port. Only the four `-o` names and values
listed above are admitted. Missing, empty, repeated conflicting, or
option-looking required values are UNKNOWN. SSH options end at the destination;
everything after it is remote shell text. SCP options end at its first source;
exactly one source and one destination must remain. Rsync admits standalone
`-a`, `--archive`, `--dry-run` before its two operands; no other short flag
groups or joined long values are admitted. A pre-operand `--` is UNKNOWN for
all three clients in this initial subset; it never silently changes option
interpretation. Repeated identical user/port declarations are permitted; disagreement with
another declaration, including `user@host`, is UNKNOWN. Values after `-o` must
be a single literal `Name=value` word with the exact case shown above.
A token beginning `-` in an operand position needs an
explicitly supported form, otherwise it is UNKNOWN. The fixture locks joined,
separate, grouped, missing-value and terminator examples.

SSH joins every argument after the destination with a space before sending it
to the remote shell. Local quoting does not protect those new boundaries:
`ssh ... cat /srv/site/VERSION ';' rm /srv/site/current` becomes a remote shell
sequence and is UNKNOWN, never a read allow. Joining a locally quoted path containing a space can create an additional
relative argument, which remains UNKNOWN (`joined-space`). A declared exact
executable with different literal arguments has no matching allow; an undeclared
opaque executable remains UNKNOWN. The supported remote shell subset
is literal `cat`, `test`, `mkdir`, `mv`, `rm` and explicitly declared exact
executable argv. Expansion, substitution, pipelines, redirection, scripts,
nested SSH, stdin/heredoc input, malformed quotes and unconsumed text are
UNKNOWN.

The following remote forms freeze grammar recognition; recognition never grants
permission. SSH allows still require an exact declared argv rule. Bare builtin
names are recognized as listed; an absolute executable name is opaque unless
that exact executable is declared in a rule.

| Remote command | Admitted literal form | Operation |
| --- | --- | --- |
| `cat` | `cat [--] PATH` with exactly one path | `read` |
| `test` | `test PREDICATE PATH`, predicate one of `-e`, `-f`, `-d`; no `--` | `read` |
| `mkdir` | `mkdir [-p] [--] PATH` with exactly one path | `create-dir` |
| `mv` | `mv [--] SOURCE DESTINATION` with exactly two paths | `rename` |
| `rm` | `rm [FLAGS] [--] PATH...` with at least one path | `remove` |

For `rm`, FLAGS are zero or more pre-path short options containing only `f`,
`r`, or `R`; separate and grouped forms are admitted. Repetitions retain their
meaning. Long options and all other flags are UNKNOWN. For the other commands,
only the flags and counts in the table are admitted; operandless commands,
extra absolute operands, options after operands, or any other option are
UNKNOWN (`unsupported-remote-syntax`). A leading optional `--` is supported
only where the table shows it; it is retained in argv for exact rule matching.
All path operands are validated before arity: a relative or otherwise
noncanonical operand gives `noncanonical-remote-path`, including a path split
by SSH argv joining. Every admitted removal operand is checked against deny
roots; one protected operand is enough to match a deny. Rename root rules
inspect both source and destination. A declared opaque executable with different
literal arguments is BLOCK/no-matching-rule, while an undeclared opaque
executable is UNKNOWN/unsupported-remote-syntax. Arguments to an opaque
executable have no inferred path or side-effect meaning.

Unsupported remote syntax takes precedence over an unmatched destination;
a syntactically supported command can be BLOCK/unmatched-destination. The
`observer-write-denied` corpus case uses supported `mkdir` to isolate that
policy decision. Server-enforced observer denial is not a parser property; the opt-in disposable SSH fixture exercises it separately.

Remote paths must be absolute and lexically normalized under the
specified root. Relative, tilde, variable and traversal paths are UNKNOWN.
The admitted command paths likewise exclude dot/empty components (a trailing
slash is required for rsync directory operands). Component prefixes do not
match: `/srv/site/releases-other` is outside `/srv/site/releases`.
Literal wrappers admit `command PROGRAM ...`, `env NAME=value ... PROGRAM ...`
and `timeout SECONDS PROGRAM ...` with a positive decimal integer duration;
wrapper flags, dynamic assignments and missing operands are UNKNOWN. Visible
transport overrides such as `RSYNC_RSH` are UNKNOWN even with a literal value.
`sh -c`/`bash -c` take exactly one literal script argument, no extra positionals.
A compound retains a recognized deny even alongside unsupported syntax;
otherwise any UNKNOWN prevents an allow. Joined remote shell separators remain
unsupported: do not speculate about an apparent rm after a quoted semicolon.
Rsync's recognized deletion flags are `--delete`, `--delete-before`,
`--delete-during`, `--delete-delay`, `--delete-after`, `--delete-excluded` and
`--delete-missing-args`; the explicit delete rule wins for those flags.
`--remove-source-files` matches the explicit remove rule. An unknown lookalike
such as `--delete-mystery` is UNKNOWN. None is part of the supported upload allow.
Local source paths remain local; the guard cannot infer their content. Rsync
archive mode may transfer symlinks and metadata, so an upload allow is not a
content safety judgment. Client version, SSH configuration, host keys and
inherited environment cannot be proved by text inspection.

Limits before expensive work: 64 KiB command, 16 KiB policy, 64 KiB registry,
512 tokens and wrapper depth 2. Count shell words and operators across each
local wrapper/remote reparse independently; a scan at any level over 512 tokens
is incomplete. Quotes do not count as tokens, quoted contents are one word
until the next parse, and separators count as tokens. Each byte ceiling is inclusive. Depth counts
literal `sh -c`/`bash -c` nesting, with the outer command at depth 0. Fixture
`input_bytes` recipes construct exact UTF-8 file bytes with Python
`json.dumps(base, ensure_ascii=False, separators=(",", ":"))` in fixture key
order, followed by `pad_count` ASCII spaces. The sample registry binds the
SHA256 of the unpadded sample policy; policy padding cases assume a matching
digest for their constructed bytes. An oversized registry is mode-unknown,
so the integrity refusal applies. Partial or over-limit scanning returns
`applicability: unknown`, `verdict: UNKNOWN`, `reason_id: scan-incomplete`;
it cannot report nonrelevance. Results use schema version 1, applicability
`relevant|nonrelevant|unknown`, verdict `ALLOW|BLOCK|UNKNOWN`, stable reason
ID, optional opaque rule ID, and `effect_scope: invocation_only`. Diagnostics
never echo the command, file content, credentials, or complete paths.

## Activation and trust

Remote guarding starts off. The owner explicitly binds a canonical project
root, relative `.harness/operations.json` path, SHA256 digest and `report` or
`enforce` mode in `$XDG_CONFIG_HOME/harness/operations-bindings.json` (default
`~/.config/harness/operations-bindings.json`). The registry is private and
outside the project. `policy bind --project ROOT --file PATH --mode
report|enforce`, `policy show` and `policy unbind --project ROOT` are the owner
CLI (`hooks/guard/operations_policy_cli.py`, or the `policy` command of the commands profile). Binding is never automatic during install or first hook use.

Existing local blocks take precedence. In report mode, BLOCK/UNKNOWN or bad
policy permits the call with a bounded warning. In enforce mode, they refuse.
If registry corruption makes mode unrecoverable, relevant or uncertain remote
calls refuse until repair, including when the prior mode was report. Off is
valid only without an activation. Complete, within-limit nonremote calls retain
baseline output. Warnings/refusals are at most 512 UTF-8 bytes. No automatic
retry or approval loop occurs. Digest drift, missing/malformed policy, wrong or
nested project, symlink/FIFO, duplicate key and size violations never broaden
permission in an activated project. Stop at nested Git/worktree roots; neither
a nested nor sibling repository inherits the parent's binding or mode. With no
separate binding there, the guard is off and baseline behavior applies. Corpus
`hook_effect` records `silent`, `warn`, or `refuse` separately from the pure
structured result: report mode warns but permits a BLOCK/UNKNOWN result;
enforce refuses it. The integrity-refusal exception applies only when a
registry exists but its mode cannot be recovered. The owner has approved this
integrity-refusal choice for this implementation (the shared plan still labels
it proposed). Incomplete scans retain UNKNOWN/scan-incomplete even in off mode;
the hook remains silent there. A fully scanned off-mode remote case returns the
activation gate's ALLOW/feature-off with no rule, rather than claiming a policy
match. Nonrelevant cases skip policy discovery even with invalid stored state.
Windows enforce activation needs separately qualified path/ACL behavior.

The policy module exposes `strict_load(policy_bytes)` and `validate_policy(policy_dict)`, each
returning a validated schema-1 policy or raising `PolicyError(error_id)`.
`resolve_binding(cwd, *, environ=None)` returns a dictionary with `mode`
(`off`, `report`, `enforce`, or `unknown`), `policy` (validated dictionary or
`None`), `project_root` (canonical bound root or `None`), and `error_id`
(`None` on success). A missing registry or unmatched root returns `off` with
no error; unreadable registry returns `unknown` with an integrity error.
Binding and unbinding validate the original project path spelling before using
its canonical registry key; an empty project argument is refused. Binding also reads the original `--file` spelling,
including the mutation-time recheck. Existing directory/`..` components work;
symlinks, missing or non-directory components cannot disappear through normalization.

Only a missing component (`ENOENT`) establishes absence. Inaccessible parents,
non-directory components and symlink ancestors are integrity errors even when
the registry file itself is missing; CLI `show` refuses these states as well.
Original path components are checked before traversing `..`; normalization
cannot hide an invalid ancestor or change the registry's containment boundary. A missing
component followed by `..` is refused before creation. Future components and
the complete registry path must fit the destination filesystem limits before
any directory or lock is created. Mutations also reserve the lock and temporary
registry basenames and their full paths, including with an existing namespace.
An unreadable nested repository boundary returns `root-scan-error` with no
policy loaded, retaining the known mode. JSON numbers that overflow to infinity
are rejected before mutation, including numbers in preserved registry metadata.
The closest matching explicit enrollment wins; farther ancestor bindings are
not scanned after that match and cannot override its mode.
The guard performs relevance scanning before calling this loader. The owner CLI
at `hooks/guard/operations_policy_cli.py` supports `bind`, `show`, and
`unbind`; `main(argv=None)` returns 0 on success or 2 with a bounded error ID
on stderr. `bind_project(project, file, mode, *, environ=None)` returns the
binding entry; `load_registry(environ=None)` and
`unbind_project(project, *, environ=None)` return the registry dictionary.
These APIs raise `PolicyError(error_id)` on refusal. Binding does not activate
interception by itself. Registry writes use
private files in an external XDG configuration directory. Accepted schema-1
entries contain `project_root`, `policy_path`, `sha256`, and `mode`; newly
written entries may also retain a canonical `binding_key` so a mismatched
stored root returns `wrong-project` with its mode intact. Root strings containing
NUL or characters the filesystem cannot encode are registry integrity errors,
including an invalid stored root paired with a valid binding key. Registry mutation
checks the pinned directory identity and target bytes before replacement. It
reopens every namespace component without following symlinks before and after
the mutation, including after waiting for the writer lock. It
validates the prospective serialized size before creating directories or a lock,
then reloads and revalidates the mutation under the lock so concurrent writers
preserve each other's entries.
Unbinding a project with no entry leaves the registry bytes, inode and missing
configuration directories or lock files unchanged. Mutating owner commands
refuse a POSIX umask that removes owner permissions before creating any
namespace or lock; normal masks `000`, `022` and `077` work. A policy file
argument ending in `/` or `/.` is refused rather than normalized into a file. Windows
binding is refused until ACL and path behavior is qualified; an absent registry
remains off and an existing one returns `unknown`/`unsupported-platform`. The digest is an edit detection aid;
the same user can replace the policy, registry, or hook.

This is a user owned pattern safeguard, not a security boundary against the
same user with unrestricted shell access. Destination labels are policy names,
not DNS or SSH identity proof. Existing SSH authentication and host key checks
stay authoritative. The offline `ssh check --file PATH [--policy PATH]` command
(`hooks/guard/ssh_operations_cli.py`, or the `ssh` command of the commands
profile) cannot connect or mutate remote state. Without `--policy` it uses the
bound policy; with it, the result is a labelled unbound preview. Hook
enforcement requires the installed full guard or a separately reviewed custom
adapter; the commands profile registers no hook. Native Windows test suites ran for this release; the
SSH integration test is Linux-only. No live host or installed-hook qualification has been run, and
Windows policy binding stays unqualified.

## Synthetic state inputs and coverage audit

All files below are fixture descriptions; the contract itself never opens them. `bound`
means canonical root `/workspace/site`, relative `.harness/operations.json`,
the sample bytes/digest and the case's recoverable mode in an external private
registry. `/fixture/key` and `/fixture/known_hosts` in commands are literal
synthetic names; the contract reads neither. `input_bytes` overrides only the indicated
file bytes. `non-utf8` carries U+DCFF as JSON `\udcff`, representing byte FF
via surrogateescape; it must yield UNKNOWN/invalid-encoding, never ALLOW.

| State | Exact synthetic change from bound |
| --- | --- |
| unbound | No enrollment or activation for cwd. |
| nested-root / nested-worktree | Only parent is bound; cwd is a separate Git repository / worktree, no own binding. |
| sibling-root / home-root / filesystem-root | Only `/workspace/site` bound; cwd is respectively `/workspace/other`, `/home/fixture`, `/`; no inheritance. |
| enrolled-non-git | Explicit enrollment of `/workspace/site`, no Git metadata, cwd contained. |
| digest-drift | Append one ASCII space to policy bytes; retain original SHA256. |
| missing-policy | Bound policy file absent; registry entry and mode remain. |
| malformed-policy | Policy bytes are exactly `{"schema_version":1,}`; registry hashes those bytes. |
| duplicate-policy-key | Policy bytes are exactly `{"schema_version":1,"schema_version":1}`; registry hashes those bytes. |
| invalid-policy-enum / invalid-policy-version | Compact sample policy with first rule decision `permit` / schema_version `2`, digest updated. |
| policy-symlink / policy-fifo | Bound policy path is a symlink to `/fixture/policy.json` / FIFO, not a regular file. |
| policy-ancestor-escape | `.harness` is a symlink to `/fixture`, outside the registered project. |
| wrong-project | Activation selected for `/workspace/site` but its stored project_root is `/workspace/other`; mode recoverable. This is corrupt activation, not ordinary sibling lookup. |
| registry-inside-project | Registry located at `/workspace/site/.harness/operations-bindings.json`; mode recoverable. |
| unsupported-platform | Windows runner with no qualified path/ACL implementation; report warns, enforce refuses. |
| corrupt-registry | Registry bytes exactly `{`; mode cannot be recovered, including if previously report. |
| policy-oversize / registry-oversize | Exact byte recipe in case; policy mode recoverable, registry mode unknown. |

The checked matrix below audits every shared-contract section and H1 boundary.
Case references resolve to exact inputs, full schema-1 outputs and S# labels in
`tests/fixtures/ssh-operations.json`. `test_coverage_matrix` checks every row and
requires every corpus case to appear. This matrix audits the contract fixture. Parser, binding, guard and installer
behavior are covered by their own tests, and no row is a claim of live host,
connector or production-server proof.

<!-- H1 coverage matrix -->
| ID | Shared/H1 boundary | Evidence / owning gate |
| --- | --- | --- |
| M01 | S1 positive deployment: read, mkdir, rename, readback, transports | `ssh-read`, `ssh-mkdir`, `ssh-rename`, `ssh-readback`, `scp-upload`, `rsync-upload` |
| M02 | S1 exact test/executable rules; rename is separate from upload | `ssh-test`, `ssh-exact-executable`, `ssh-executable-extra-argument`, `rename-not-upload`, `read-not-allowed` |
| M03 | S1/S2 deny precedence and destination identity | `protected-rm`, `rsync-delete`, `wrong-host`, `wrong-user`, `wrong-port`, `observer-write-denied`, `root-component-prefix` |
| M04 | S2 literal/default destination and conflicting declarations | `ssh-defaults`, `conflicting-user`, `conflicting-port`, `same-user-port`, `scp-conflicting-port` |
| M05 | S2 exact SSH option arity, separate/joined/grouped values | `ssh-joined-options`, `ssh-combined-flags`, `ssh-all-options`, `ssh-joined-identity`, `ssh-group-value-joined`, `ssh-group-value-separate` |
| M06 | S2 exact SCP option arity, preserve flag versus port | `scp-joined-options`, `scp-combined-flags`, `scp-all-options`, `scp-joined-identity`, `scp-group-value-separate` |
| M07 | S2 absent/empty/option-looking values | `ssh-missing-value`, `scp-missing-value`, `ssh-missing-l`, `ssh-missing-i`, `ssh-missing-o`, `scp-missing-i`, `scp-missing-o`, `empty-option-value`, `option-looking-value`, `scp-empty-value`, `scp-option-looking-value` |
| M08 | S2 option terminators, operand positions, exact reviewed -o subset | `ssh-terminator`, `scp-terminator`, `rsync-terminator`, `ssh-post-destination-option`, `scp-post-source-option`, `scp-option-operand`, `unreviewed-o`, `scp-unreviewed-o`, `ssh-unknown-option` |
| M09 | S2 SSH configuration is not read/evaluated; interactive/forwarding/proxies | `interactive`, `forwarding`, `tunnel`, `proxycommand`, `proxyjump`, `ssh-config` |
| M10 | S2 SCP one source/destination, SFTP-only; ambiguous colon | `legacy-scp`, `recursive-scp`, `remote-to-remote-scp`, `scp-remote-to-remote-mode`, `scp-ambiguous-colon`, `scp-multiple-sources`, `scp-missing-destination`, `scp-download` |
| M11 | S2 rsync archive/dry-run and unsupported flags/transport | `rsync-long-options`, `rsync-relative`, `rsync-e`, `rsync-rsh`, `rsync-path`, `rsync-combined`, `rsync-long-value`, `rsync-option-after-source`, `rsync-environment` |
| M12 | S2 rsync operand shape, daemon syntax, literal user and trailing slashes | `rsync-daemon`, `rsync-daemon-url`, `rsync-source-no-slash`, `rsync-dest-no-slash`, `rsync-missing-user`, `rsync-multiple-sources` |
| M13 | S1/S2 explicit deletion/source-removal denies versus speculative deny | `rsync-source-removal`, `rsync-delete-before`, `rsync-delete-during`, `rsync-delete-delay`, `rsync-delete-after`, `rsync-delete-excluded`, `rsync-delete-unknown`, `rsync-deny-with-unsupported`, `speculative-deny`, `rsync-delete-missing-args` |
| M14 | S2 remote argv joining, escape/quote consumption | `joined-argv-injection`, `escaped-metacharacter`, `joined-space`, `escaped-literal`, `unmatched-quote`, `remote-quote-unmatched` |
| M15 | S2 local/remote paths are distinct, no HOME/cwd/symlink inference | `remote-path-escape`, `relative-path`, `tilde-path`, `scp-relative-path`, `scp-tilde-path`, `scp-variable-path`, `rsync-relative-path`, `ssh-dot-path`, `scp-empty-component`, `rsync-tilde-path`, `rsync-variable-path` |
| M16 | S2 dynamic syntax, substitutions, redirects, scripts, nested SSH | `expansion`, `remote-pipeline`, `remote-redirect`, `remote-substitution`, `remote-backticks`, `nested-ssh`, `remote-script`, `encoded-shell`, `heredoc`, `stdin-script`, `local-substitution`, `local-pipeline`, `unconsumed-background`, `dynamic-destination`, `opaque`, `remote-heredoc` |
| M17 | S2 wrappers and compound aggregation | `wrapper`, `command-wrapper`, `bash-wrapper`, `compound`, `compound-semicolon`, `compound-or`, `compound-deny`, `compound-unknown`, `incomplete-wrapper`, `wrapper-option`, `wrapper-extra-argument`, `wrapper-dynamic`, `wrapper-missing-command` |
| M18 | S2 complete command-position relevance versus opaque coverage boundary | `literal-nonremote`, `opaque-local`, `argument-not-command`, `nonremote-unknown-quoting` |
| M19 | S2 byte/token/depth ceilings and incomplete scan beyond prefix | `late-ssh-limit`, `benign-limit`, `command-limit-at`, `command-limit-over`, `token-limit-at`, `token-limit-over`, `depth-two`, `depth-three`, `non-utf8`, `token-limit-next`, `byte-limit-multibyte` |
| M20 | S3 policy/registry exact size ceilings; recoverable report warning | `policy-limit-at`, `policy-limit-over`, `registry-limit-at`, `registry-limit-over`, `report-policy-limit-over`, `policy-oversize-nonremote` |
| M21 | S3 report/off/enforce table and integrity exception; nonrelevant parity | `report-deny`, `report-unknown`, `report-digest-drift`, `report-allow`, `report-scan-incomplete`, `off-parity`, `off-remote`, `off-scan-incomplete`, `corrupt-registry`, `corrupt-registry-nonremote`, `corrupt-registry-incomplete`, `digest-drift` |
| M22 | S3 no inheritance into nested repositories, worktrees, siblings, HOME/root; enrollment | `nested-root`, `sibling-root`, `nested-worktree`, `home-root`, `filesystem-root`, `enrolled-non-git` |
| M23 | S3 missing-policy with recoverable report/enforce mode | `report-missing-policy`, `enforce-missing-policy` |
| M24 | S3 malformed-policy with recoverable report/enforce mode | `report-malformed-policy`, `enforce-malformed-policy` |
| M25 | S3 duplicate-policy-key with recoverable report/enforce mode | `report-duplicate-policy-key`, `enforce-duplicate-policy-key` |
| M26 | S3 invalid-policy-enum with recoverable report/enforce mode | `report-invalid-policy-enum`, `enforce-invalid-policy-enum` |
| M27 | S3 invalid-policy-version with recoverable report/enforce mode | `report-invalid-policy-version`, `enforce-invalid-policy-version` |
| M28 | S3 policy-symlink with recoverable report/enforce mode | `report-policy-symlink`, `enforce-policy-symlink` |
| M29 | S3 policy-fifo with recoverable report/enforce mode | `report-policy-fifo`, `enforce-policy-fifo` |
| M30 | S3 policy-ancestor-escape with recoverable report/enforce mode | `report-policy-ancestor-escape`, `enforce-policy-ancestor-escape` |
| M31 | S3 wrong-project with recoverable report/enforce mode | `report-wrong-project`, `enforce-wrong-project` |
| M32 | S3 registry-inside-project with recoverable report/enforce mode | `report-registry-inside-project`, `enforce-registry-inside-project` |
| M33 | S3 unsupported-platform with recoverable report/enforce mode | `report-unsupported-platform`, `enforce-unsupported-platform` |
| M34 | Remote builtin arity, flags, operation and unsupported/destination precedence | `remote-cat-empty`, `remote-cat-option`, `remote-cat-many`, `remote-cat-terminator`, `remote-test-empty`, `remote-test-option`, `remote-test-many`, `remote-test-exists`, `remote-test-directory`, `remote-mkdir-empty`, `remote-mkdir-mode`, `remote-mkdir-many`, `remote-mkdir-bare`, `remote-mkdir-terminator`, `remote-mv-missing`, `remote-mv-option`, `remote-mv-many`, `remote-mv-terminator`, `remote-rm-empty`, `remote-rm-force`, `remote-rm-recursive`, `remote-rm-separate-flags`, `remote-rm-many`, `remote-rm-unlisted`, `remote-rm-long-option`, `remote-rm-option-after-path`, `remote-absolute-undeclared`, `wrong-destination-unknown-command` |
<!-- /H1 coverage matrix -->

| ID | Remaining shared-contract bullet/table/claim | Owning gate and exact required proof |
| --- | --- | --- |
| D01 | S1–S3 pure API/no execution, spans/provenance, full consumption, no filesystem/DNS/config inspection; no speculative deny | H2: run this corpus against inspect_remote(command, policy, cwd); trap subprocess/network/filesystem resolution; preserve schema/version/reasons/scope. H1 only validates data. |
| D02 | Schema/trust: no regex/glob/host exemption/secret fields; unique IDs/keys; malformed version/enum/shape | H1 test_valid_policy_shapes, test_invalid_policy_shapes, test_duplicate_and_malformed_rules_rejected; H3 strict loader uses same constraints. |
| D03 | Binding CLI/root/path/SHA256/mode; private external registry and XDG fallback; explicit enrollment; no auto binding/migration by hook/install/uninstall | H3: bind/show/unbind and revoke/rebind; atomic exclusive no-follow updates; bounded root lookup without git; unchanged unrelated entries/files after failure; symlink cwd and Windows spelling/ACL qualification. State rows above fix classification outcomes. |
| D04 | Existing local blocks win; baseline stdout/stderr/exit/context; <=512 UTF-8 warning/refusal bytes, no raw command/paths/content; adapter conventions; no approval/retry | H4: local refusal before remote evaluation in every mode; snapshot baseline fields; budget/redaction assertions for warnings/refusals, including over-limit exceptions. |
| D05 | S4 cost proof: >=200 serial warm samples, p50/p95/runner, Wave 0 thresholds; unchanged ordinary/catalog bytes/budgets; no routine model/network calls | H4: pinned baseline/candidate benchmark, explicit warning/command tokens separately accounted; bytes/4 remains estimate, changes justified independently. |
| D06 | Offline ssh check --file PATH or explicit finite stdin, schema-1 command/project_root, bound policy versus labelled unbound --policy preview | H4: regular nonblocking input before read, no activation/network/remote writes; exit 0 ALLOW/nonrelevant, 3 BLOCK, 4 UNKNOWN, 1 malformed/unavailable, 2 argument error. |
| D07 | S5 native/custom roles: tools/model/inheritance and observable exclusion preserved through accounting faults | Router R1/R4: synthetic passthrough/fault tests and paired workflow; no Harness H1 routing change. |
| D08 | S6 installers: default full; opt-in commands, one explicit claude or codex host; both full-only; write-free dry-run, saved plan/exact apply, rollback/recover/uninstall | Router C1 + Harness H6: scoped transactional lifecycle tests, no live installation in H1. |
| D09 | All four product/host namespace table rows: separate owned runtime, launcher and default XDG_STATE_HOME namespaces; ~/.local/state fallback and documented host-home overrides | C1/H6/R3: verify Router/Harness x Claude/Codex paths from shared table; protect global router/private overlays/other hosts; launcher conflict before mutation; no automatic profile/full ownership switching. |
| D10 | Runtime allowlist operations/observe/receipt(Router)/usage/evaluate/status/doctor/help/version and Harness ssh/policy; reject routing/on/off/auto/ladder/config before loading state | C2/H6: exact allow/reject tests; isolated ROUTER_COMMANDS_STATE/HARNESS_COMMANDS_STATE overrides, legacy variables ineffective; truthful commands-only/unregistered-hook status. Binding alone creates no interception. |
| D11 | Distinct manifest/journal ownership, paths/hashes/modes/revision; modified/extra files, symlink parents/journals, interruptions, concurrent aliases | C1/H6/R3: drift refusal before mutation, reversal/recovery inventories preserve unrelated config/command data; byte-identical Router-owned helper mirror; no private paths/data. |
| D12 | S7 real readback and server observer write denial, actual OpenSSH/rsync versions and default SFTP, archive symlink/metadata caveat | H5: pinned disposable fixture, actual bytes/readback/server denial; supplied JSON/static tool visibility and observer-write-denied corpus case are not live proof. No remote-content/identity claim from ALLOW. |
| D13 | S8 exact platform/host/commits, tests versus installation/live qualification; preserve SSH keys/agents/verification/config/billing | H5/H6/Router R5: explicit qualification/evidence; no private credentials in policy/evidence; same-user bypass remains possible, server permissions are the hard boundary. |
| D14 | Shared primary references and trust limits; release sequence and separate publication/website approvals | H1 freezes only the listed subset; H2/H5 verify client behavior against pinned clients. H6/R5 own release evidence and separate publication. No parser/installer/live-server/model invocation occurs here. |

Review regression mapping: review 1 findings 1–4 are covered by the full-result
schema tests, M22, M05–M08, and M19–M21 respectively. Review 2 finding 1 is
`test_report_oversized_policy_warns` plus `report-policy-limit-over`; finding 2
is M04 and M09–M17. Matrix checking prevents removing a required boundary case
without updating both the frozen requirement list and the documented audit.
