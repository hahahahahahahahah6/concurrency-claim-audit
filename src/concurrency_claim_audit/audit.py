"""Offline documentation claims and AST evidence inspection."""
import argparse
import ast
import fnmatch
import json
import os
from pathlib import Path
import re
import sys

PHRASES = {
    'parallel': ('in parallel', 'parallel processing', 'parallel execution',
                 'concurrently', 'concurrent processing', 'concurrent execution',
                 'multithreaded', 'multi-threaded', 'multiprocessing', 'multi-process',
                 'multiple threads', 'multiple workers', 'thread pool', 'worker pool', 'process pool'),
    'sequential': ('single-threaded', 'single threaded', 'sequential processing',
                   'sequential execution', 'runs sequentially', 'run sequentially'),
}
NEGATION = re.compile(r"\b(?:no|not|never|without|doesn't|don't|isn't)\b", re.I)
CONSTRUCTORS = {'Thread', 'Process', 'Pool', 'ThreadPoolExecutor', 'ProcessPoolExecutor'}
ASYNC_CALLS = {'gather', 'create_task', 'TaskGroup', 'wait'}


def ignored(path, patterns):
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def files(root, patterns):
    """Never follow directory or file symlinks; never inspect .env files."""
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink()
                         and d not in {'.git', '.venv', 'venv', '__pycache__', 'dist', 'build'}
                         and not ignored((Path(directory) / d).relative_to(root).as_posix(), patterns))
        for name in sorted(names):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if not path.is_symlink() and name != '.env' and not name.startswith('.env.') and not ignored(rel, patterns):
                yield path, rel


def extract_claims(text, path):
    lines = []
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = None
            lines.append((number, ''))
        else:
            lines.append((number, line if fence is None else ''))
    # Keep paragraph and sentence boundaries while allowing phrases across lines.
    prose = '\n'.join(line for _, line in lines)
    claims = []
    for match in re.finditer(r'[^.!?\n]+(?:\n(?!\s*\n)[^.!?\n]+)*[.!?]?', prose):
        sentence = match.group()
        if NEGATION.search(sentence):
            continue
        for family, phrases in PHRASES.items():
            for phrase in phrases:
                found = re.search(r'(?<!\w)' + re.escape(phrase).replace(r'\ ', r'\s+') + r'(?!\w)', sentence, re.I)
                if found:
                    offset = match.start() + found.start()
                    claims.append({'family': family, 'path': path, 'line': prose.count('\n', 0, offset) + 1,
                                   'phrase': found.group()})
    return claims


def extract_evidence(text, path):
    tree = ast.parse(text, filename=path)
    aliases = {'asyncio': 'asyncio'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split('.')[0]] = item.name
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                aliases[item.asname or item.name] = (node.module or '') + '.' + item.name

    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return qualified(node.value) + '.' + node.attr
        return ''

    result = {}
    for node in ast.walk(tree):
        kinds = []
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = ([i.name for i in node.names] if isinstance(node, ast.Import)
                       else [node.module or ''] + [(node.module or '') + '.' + i.name for i in node.names])
            if any(m == 'threading' or m.startswith('threading.') or m == 'multiprocessing'
                   or m.startswith('multiprocessing.') or m == 'concurrent.futures'
                   or m.startswith('concurrent.futures.') for m in modules):
                kinds.append('concurrency-import')
        if isinstance(node, ast.Call):
            name = qualified(node.func)
            if name.split('.')[-1] in CONSTRUCTORS:
                kinds.append('concurrency-call')
            if name in {'asyncio.' + item for item in ASYNC_CALLS}:
                kinds.append('asyncio-call')
        for kind in kinds:
            result[(path, node.lineno, kind)] = {'path': path, 'line': node.lineno, 'kind': kind}
    return sorted(result.values(), key=lambda e: (e['path'], e['line'], e['kind']))


def audit(root, patterns=()):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('audit root must be a real directory')
    claims, production, testonly, errors = [], [], [], []
    source_count = 0
    for path, rel in files(root, patterns):
        parts = Path(rel).parts
        doc = rel == 'README.md' or (parts[0] == 'docs' and path.suffix == '.md')
        test = 'tests' in parts[:-1] or 'fixtures' in parts[:-1] or path.name.startswith('test_') or path.name.endswith('_test.py')
        source = path.suffix == '.py' and 'docs' not in parts[:-1] and 'examples' not in parts[:-1]
        if not (doc or source):
            continue
        if source and not test:
            source_count += 1
        try:
            text = path.read_text(encoding='utf-8-sig')
            if doc:
                claims.extend(extract_claims(text, rel))
            if source:
                (testonly if test else production).extend(extract_evidence(text, rel))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({'path': rel, 'error': str(exc)})
    if errors:
        raise ValueError('static analysis failed: ' + json.dumps(errors))
    findings, verified = [], []
    for family in PHRASES:
        selected = [c for c in claims if c['family'] == family]
        if not selected:
            continue
        severity, evidence, suffix = None, [], ''
        if not source_count:
            severity, evidence = 'HIGH', [{'kind': 'no-source'}]
        elif family == 'parallel' and not production:
            severity = 'MEDIUM' if testonly else 'HIGH'
            evidence = testonly
            suffix = '-test-only' if testonly else ''
        elif family == 'sequential' and (production or testonly):
            severity = 'HIGH' if production else 'MEDIUM'
            evidence = production or testonly
            suffix = '' if production else '-test-only'
        if severity:
            findings.append({'rule': 'documented-' + family + '-claim-false' + suffix,
                             'severity': severity, 'family': family, 'claims': selected, 'evidence': evidence})
        else:
            verified.append({'family': family, 'claims': selected, 'evidence': production if family == 'parallel' else []})
    return {'findings': findings, 'verified_claims': verified,
            'production_evidence': production, 'test_only_evidence': testonly}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, self.prog + ': error: ' + message + '\n')


def main(argv=None):
    parser = Parser(prog='concurrency-claim-audit', description=__doc__)
    parser.add_argument('path', nargs='?', default='.', help='project tree (default: current directory)')
    parser.add_argument('--json', action='store_true', dest='as_json')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[], metavar='GLOB')
    parser.add_argument('--fail-on', choices=['MEDIUM', 'HIGH'], default='MEDIUM')
    parser.add_argument('--version', action='version', version='0.1.0')
    args = parser.parse_args(argv)
    try:
        result = audit(args.path, args.ignore)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    failed = any(f['severity'] == 'HIGH' or args.fail_on == 'MEDIUM' for f in result['findings'])
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print('FAIL' if failed else 'PASS')
        for finding in result['findings']:
            print(finding['severity'] + ' ' + finding['rule'])
            if args.verbose:
                print(json.dumps(finding, indent=2))
        if args.verbose:
            print('Verified claims: ' + json.dumps(result['verified_claims']))
    return 2 if failed else 0
