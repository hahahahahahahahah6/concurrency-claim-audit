# concurrency-claim-audit

Offline audit of documented concurrency claims against Python source.

A standard-library-only, static CLI. It reads documentation and parses source;
it executes no audited code, uses no network, and never reads `.env` contents.
It skips symlinked files and directories and reads only the selected tree.

## Usage

Requires Python 3.9 or later. Install and run:

```sh
python3 -m pip install .
concurrency-claim-audit /path/to/project
python3 -m concurrency_claim_audit /path/to/project --json
concurrency-claim-audit . --verbose --ignore 'vendor/*' --fail-on HIGH
concurrency-claim-audit --version
```

The path defaults to the current directory. `--ignore GLOB` is repeatable and
matches relative POSIX paths (including document paths). `--verbose` includes
claim locations and evidence. JSON always includes `findings`, `verified_claims`,
`production_evidence`, and `test_only_evidence`. Findings contain `rule`,
`severity`, `family`, all `claims`, and all applicable `evidence` items.

Exit status: 0 for PASS, 2 for findings meeting the threshold, 1 for usage or
analysis errors. The default threshold is MEDIUM; `--fail-on HIGH` retains
MEDIUM findings in output while allowing a MEDIUM-only audit to pass.
Malformed or unreadable source is an analysis error rather than a silent PASS.

## Rules

| Claim family | Source evidence | Result |
| --- | --- | --- |
| parallel | production evidence | verified |
| parallel | no evidence anywhere | HIGH `documented-parallel-claim-false` |
| parallel | test-only evidence | MEDIUM `documented-parallel-claim-false-test-only` |
| sequential | production evidence | HIGH `documented-sequential-claim-false` |
| sequential | test-only evidence | MEDIUM `documented-sequential-claim-false-test-only` |
| sequential | no evidence anywhere | verified |
| either family | no production Python source | HIGH family finding, `no-source` evidence |
| no claims | any valid source | PASS |

One finding is emitted per family, collecting every relevant evidence item.
Both families are evaluated independently. No-source takes precedence over
test-only evidence. A test-only sequential finding is a deliberately cautious
signal, even though production source has no evidence.

Documentation sources are root `README.md` and `docs/**/*.md`. Fenced code
blocks are excluded; inline code spans count as prose. Claims are exact phrases,
case insensitive, with these spellings only:

- Parallel family: “in parallel”, “parallel processing”, “parallel execution”,
  “concurrently”, “concurrent processing”, “concurrent execution”, “multithreaded”,
  “multi-threaded”, “multiprocessing”, “multi-process”, “multiple threads”,
  “multiple workers”, “thread pool”, “worker pool”, “process pool”.
- Sequential family: “single-threaded”, “single threaded”, “sequential processing”,
  “sequential execution”, “runs sequentially”, “run sequentially”.

A sentence containing no, not, never, without, doesn't, don't, or isn't suppresses
all its claims. Bare “parallel” and “concurrent” are insufficient.

The stdlib-only evidence table is:

| AST construct | Evidence |
| --- | --- |
| import/from import of threading, multiprocessing, concurrent.futures | concurrency-import |
| call named Thread, Process, Pool, ThreadPoolExecutor, ProcessPoolExecutor | concurrency-call |
| asyncio.gather, asyncio.create_task, asyncio.TaskGroup, asyncio.wait calls | asyncio-call |

Aliases are resolved for imports, including asyncio calls. Constructor calls
count with any receiver. Items deduplicate by relative path, line, and kind.
Production excludes `tests/`, `fixtures/`, `test_*.py`, `*_test.py`, `docs/`, and
`examples/`; test and fixture evidence is collected separately. Documentation
and example source never contributes evidence. Symlinks, environment files,
and `.git`, `.venv`, `venv`, `__pycache__`, `dist`, `build` directories are skipped.

## Differentiation

| Neighbour | Its focus |
| --- | --- |
| streaming-claim-audit | Read patterns and memory posture; execution parallelism is outside its scope |
| determinism-claim-audit | Run-to-run output stability; thread/process use is outside its scope |
| security-claim-audit | eval, shell, pickle; concurrency primitives are outside its scope |
| platform-claim-audit | OS availability; execution model is outside its scope |
| bandit / ruff | Code issues; documentation promises are outside their scope |
| pytest / timing benchmarks | Runtime observations; static docs-versus-code truth is outside their scope |

## Honest limitations

Exact-phrase claims miss synonyms and nuanced promises. Negation suppresses the
whole sentence, including negation-phrased sequential claims such as “no
parallelism”; express sequential claims with a positive listed phrase.
Sentence splitting is heuristic (punctuation and paragraph boundaries), not a
natural-language parser. Markdown HTML and nested fence structures are not fully
parsed. Whitespace within a phrase may span lines.

Imports intentionally over-approximate behaviour: a locks-only file with
`import threading` counts as evidence. Lock, RLock, Event, and Semaphore calls
alone provide no evidence; their threading import does. Dead code counts,
including uncalled functions. Evidence does not establish speedup, actual
simultaneous execution, worker counts, or correctness. Name-based constructors
may refer to unrelated user classes. Alias resolution is static and does not
model reassignment or scope shadowing.

A bare `import asyncio` is insufficient: a single-task event loop is possible.
Third-party concurrency libraries (joblib, ray, dask, celery) are outside the
stdlib-only evidence table. `subprocess` and `os.fork` are excluded: external
command spawning does not establish the execution model this audit checks.
There is no runtime execution or timing measurement.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

The wheel installs the CLI and library. The sdist also includes unittest tests
and all sample fixtures via `MANIFEST.in`. No runtime dependencies are required.
