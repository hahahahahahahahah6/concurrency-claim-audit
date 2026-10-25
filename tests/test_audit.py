import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from concurrency_claim_audit.audit import PHRASES, audit, extract_claims, extract_evidence, main


class AuditTests(unittest.TestCase):
    def project(self, docs='in parallel', source='pass', extra=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'README.md').write_text(docs, encoding='utf-8')
        if source is not None:
            (root / 'main.py').write_text(source, encoding='utf-8')
        for name, text in (extra or {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        return root

    def test_parallel_missing(self):
        f = audit(self.project())['findings']
        self.assertEqual([x['rule'] for x in f], ['documented-parallel-claim-false'])
        self.assertEqual(f[0]['evidence'], [])

    def test_parallel_verified(self):
        self.assertEqual(audit(self.project(source='import threading'))['verified_claims'][0]['family'], 'parallel')

    def test_sequential_verified(self):
        self.assertEqual(audit(self.project('single-threaded'))['verified_claims'][0]['family'], 'sequential')

    def test_sequential_all_evidence(self):
        result = audit(self.project('single-threaded', 'import threading\nthreading.Thread()'))
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(len(result['findings'][0]['evidence']), 2)

    def test_both_no_source(self):
        result = audit(self.project('in parallel. single-threaded.', None))
        self.assertEqual(len(result['findings']), 2)
        self.assertTrue(all(f['evidence'] == [{'kind': 'no-source'}] for f in result['findings']))

    def test_both_independent_testonly(self):
        result = audit(self.project('in parallel. single-threaded.', extra={'tests/work.py': 'import threading'}))
        self.assertEqual(len(result['findings']), 2)
        self.assertTrue(all(f['severity'] == 'MEDIUM' for f in result['findings']))

    def test_ignore(self):
        root = self.project(source='import threading', extra={'other.py': 'pass'})
        self.assertEqual(audit(root, ['main.py'])['findings'][0]['rule'], 'documented-parallel-claim-false')

    def test_no_claim(self):
        self.assertEqual(audit(self.project('Hello', 'import threading'))['findings'], [])

    def test_docs_nested(self):
        self.assertEqual(len(audit(self.project('Hello', extra={'docs/nested/a.md': 'in parallel'}))['findings']), 1)

    def test_other_docs_ignored(self):
        self.assertEqual(audit(self.project('Hello', extra={'OTHER.md': 'in parallel'}))['findings'], [])

    def test_symlink_source_skipped(self):
        root = self.project()
        (root / 'linked.py').symlink_to(root / 'main.py')
        self.assertEqual(len(audit(root)['findings']), 1)

    def test_symlink_directory_skipped(self):
        root = self.project()
        (root / 'linked').symlink_to(root, target_is_directory=True)
        self.assertEqual(len(audit(root)['findings']), 1)

    def test_env_unread(self):
        root = self.project()
        (root / '.env').write_bytes(b'\xff')
        self.assertEqual(len(audit(root)['findings']), 1)

    def test_syntax_error_usage(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main([str(self.project(source='bad syntax!'))])
        self.assertEqual(caught.exception.code, 1)

    def test_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([str(self.project()), '--json'])
        self.assertEqual(code, 2)
        self.assertTrue({'findings', 'verified_claims'} <= json.loads(out.getvalue()).keys())

    def test_fail_on_high(self):
        root = self.project(extra={'tests/test_work.py': 'import threading'})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(root), '--fail-on', 'HIGH']), 0)
            self.assertEqual(main([str(root)]), 2)

    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            main(['--version'])
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), '0.1.0')

    def test_usage(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(['--unknown'])
        self.assertEqual(caught.exception.code, 1)

    def test_fence(self):
        self.assertEqual(extract_claims('```text\nin parallel\n```\n~~~\nsingle-threaded\n~~~', 'README.md'), [])

    def test_inline(self):
        self.assertEqual(len(extract_claims('Uses `parallel processing`.', 'README.md')), 1)

    def test_sentence_negation_scope(self):
        self.assertEqual(len(extract_claims('Not parallel processing. Runs sequentially.', 'README.md')), 1)

    def test_lock_import(self):
        self.assertEqual(len(extract_evidence('import threading\nthreading.Lock()', 'a.py')), 1)

    def test_bare_asyncio(self):
        self.assertEqual(extract_evidence('import asyncio\nasyncio.run(work())', 'a.py'), [])

    def test_alias_asyncio(self):
        self.assertEqual(len(extract_evidence('import asyncio as a\na.gather()', 'a.py')), 1)

    def test_from_asyncio(self):
        self.assertEqual(len(extract_evidence('from asyncio import create_task as task\ntask(x)', 'a.py')), 1)

    def test_from_concurrent_import(self):
        self.assertEqual(len(extract_evidence('from concurrent import futures as f', 'a.py')), 1)

    def test_alias_constructor(self):
        self.assertEqual(len(extract_evidence('from concurrent.futures import ThreadPoolExecutor as E\nE()', 'a.py')), 2)

    def test_dead_code(self):
        self.assertEqual(len(extract_evidence('def unused():\n    Thread()', 'a.py')), 1)

    def test_dedupe(self):
        self.assertEqual(len(extract_evidence('Thread(); Thread()', 'a.py')), 1)

    def test_subprocess_fork(self):
        self.assertEqual(extract_evidence('import subprocess, os\nsubprocess.run([])\nos.fork()', 'a.py'), [])

    def test_third_party(self):
        self.assertEqual(extract_evidence('import joblib, ray, dask, celery\nray.remote(work)', 'a.py'), [])

    def test_fixture_contracts(self):
        base = Path(__file__).parent / 'fixtures'
        expected = {'good/parallel': None, 'good/sequential': None,
                    'bad/parallel': 'documented-parallel-claim-false',
                    'bad/sequential': 'documented-sequential-claim-false',
                    'testonly': 'documented-parallel-claim-false-test-only'}
        for path, rule in expected.items():
            with self.subTest(path=path):
                result = audit(base / path)
                self.assertEqual([f['rule'] for f in result['findings']], [rule] if rule else [])


def phrase_test(family, phrase):
    def test(self):
        claims = extract_claims('The tool uses ' + phrase.upper() + '.', 'README.md')
        self.assertEqual([c['family'] for c in claims], [family])
    return test


for family, phrases in PHRASES.items():
    for index, phrase in enumerate(phrases):
        setattr(AuditTests, 'test_phrase_' + family + '_' + str(index), phrase_test(family, phrase))


def negative_test(text):
    def test(self):
        self.assertEqual(extract_claims(text, 'README.md'), [])
    return test


for index, text in enumerate(['parallel universes', 'parallel lines', 'concurrent users',
                               'parallel fast processing', 'concurrent fast execution',
                               'no parallelism', 'not in parallel', 'never multithreaded',
                               'without parallel processing', "doesn't run sequentially",
                               "don't use multiple workers", "isn't single-threaded"]):
    setattr(AuditTests, 'test_nonclaim_' + str(index), negative_test(text))


def evidence_test(source):
    def test(self):
        self.assertTrue(extract_evidence(source, 'main.py'))
    return test


for index, source in enumerate(['import threading as t', 'import multiprocessing as m',
                                 'from concurrent import futures\nfutures.ThreadPoolExecutor()',
                                 'from threading import Thread as T', 'x.Process()', 'x.Pool()',
                                 'ProcessPoolExecutor()', 'asyncio.gather()', 'asyncio.wait()',
                                 'asyncio.create_task(work)', 'asyncio.TaskGroup()']):
    setattr(AuditTests, 'test_evidence_' + str(index), evidence_test(source))


def exclusion_test(name, expected):
    def test(self):
        result = audit(self.project(extra={name: 'import threading'}))
        self.assertEqual(result['findings'][0]['severity'], expected)
    return test


for index, (name, severity) in enumerate([('tests/work.py', 'MEDIUM'), ('fixtures/work.py', 'MEDIUM'),
                                         ('test_work.py', 'MEDIUM'), ('work_test.py', 'MEDIUM'),
                                         ('examples/work.py', 'HIGH'), ('docs/work.py', 'HIGH')]):
    setattr(AuditTests, 'test_exclusion_' + str(index), exclusion_test(name, severity))
