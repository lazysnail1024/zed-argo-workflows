#!/usr/bin/env python3
"""Selected byte-material symlinks through the real receipt materialization phase.

The optional profiles path replays an existing complete selected consumer with
the production validator. It is not a new native/extended lane execution.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "tests/ci/control.py"
TRANSPORT = ROOT / "tests/ci/transport.py"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--producer-only", action="store_true", help="Exercise portable selected file links without FIFO or POSIX hydration")
parser.add_argument("--profiles", type=Path)
parser.add_argument("--binary", type=Path)
args = parser.parse_args()
out = args.output.resolve();out.mkdir(parents=True, exist_ok=False)
rows = []


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(name, passed, **details):
    rows.append({"case": name, "pass": bool(passed), **details})
    (out / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(name, "PASS" if passed else "FAIL", flush=True)


# Before the helper existed, execute the exact materialization statement from
# control.receipt's AST. This compatibility adapter has no independent copy of
# the move algorithm and supplies no substitute domain acceptance. The current
# path calls the same production helper as control.receipt.
PHASE = r'''
import ast,hashlib,json,os,shutil,stat,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1]);import control
root,payload,request=map(Path,sys.argv[2:5]);value=json.loads(request.read_text())
files=[root/name for name in value['files']];evidence=value['evidence'];fault=value.get('fault')
links=control.snapshot_file_links(files) if hasattr(control,'snapshot_file_links') else None
metadata={}
for source in files:
 if source.is_symlink():
  path_stat=source.stat()
  with source.open('rb') as stream:fd_stat=os.fstat(stream.fileno())
  keys=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_birthtime_ns','st_mode')
  metadata[source.relative_to(root).as_posix()]={
   'path':{key:getattr(path_stat,key,None) for key in keys},
   'fd':{key:getattr(fd_stat,key,None) for key in keys}}
selected=root/'link.yaml';target=root/'target.txt';observed=False;injected=False
if fault=='dangling':
 target.unlink();injected=not os.path.lexists(target) and selected.is_symlink()
if fault=='nonregular':
 target.unlink();os.mkfifo(target);injected=stat.S_ISFIFO(target.lstat().st_mode) and selected.is_symlink()
if fault=='retarget':
 other=root/'other.txt';other.write_bytes(target.read_bytes());selected.unlink();selected.symlink_to(other)
 injected=selected.is_symlink() and selected.resolve()==other.resolve() and other.read_bytes()==target.read_bytes()
if fault=='changed-during-read':
 original=os.read;wanted=target.stat()
 def read(fd,count):
  global observed
  result=original(fd,count);current=os.fstat(fd)
  if not observed and (current.st_dev,current.st_ino)==(wanted.st_dev,wanted.st_ino):
   target.write_bytes(b'x'*wanted.st_size);os.utime(target,ns=(wanted.st_atime_ns,wanted.st_mtime_ns))
   observed=(target.read_bytes()==b'x'*wanted.st_size and target.stat().st_mtime_ns==wanted.st_mtime_ns)
  return result
 os.read=read
try:
 if hasattr(control,'materialize_files'):
  control.materialize_files(files,root,payload,evidence,True,links)
 else:
  tree=ast.parse(Path(control.__file__).read_text());function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='receipt')
  node=next(n for n in function.body if isinstance(n,ast.If) and isinstance(n.test,ast.Attribute) and n.test.attr=='move_payload')
  scope={'args':SimpleNamespace(move_payload=True,evidence=root),'value':{'workflow':'native'},'files':files,'payload':payload,'shutil':shutil,'require':control.require}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),control.__file__,'exec'),scope)
 print(json.dumps({'completed':True,'observedReadMutation':observed,'injectedFault':injected or observed,'metadata':metadata}))
except Exception as error:
 print(json.dumps({'completed':False,'observedReadMutation':observed,'injectedFault':injected or observed,'metadata':metadata,
  'error':str(error),'errorType':type(error).__name__,'errorFilename':getattr(error,'filename',None)}));sys.exit(1)
'''


def materialize(name, source, payload, files, expected, fault=None):
    request = out / (name + ".request.json")
    request.write_text(json.dumps({"files": files, "evidence": expected, "fault": fault}, indent=2) + "\n")
    command = [sys.executable, "-c", PHASE, str(ROOT / "tests/ci"), str(source), str(payload), str(request)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
    (out / (name + ".stdout.raw")).write_bytes(result.stdout)
    (out / (name + ".stderr.raw")).write_bytes(result.stderr)
    (out / (name + ".command.json")).write_text(json.dumps({"command": command, "exitCode": result.returncode}, indent=2) + "\n")
    details = json.loads(result.stdout) if result.stdout else {}
    return result.returncode, details


def codec(name, *arguments):
    command = [sys.executable, str(TRANSPORT), *map(str, arguments)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=60)
    (out / (name + ".stdout.raw")).write_bytes(result.stdout)
    (out / (name + ".stderr.raw")).write_bytes(result.stderr)
    return result.returncode


def finish(expected):
    assert {row['case'] for row in rows} == expected and len(rows) == len(expected)
    (out / "manifest.json").write_text(json.dumps({"command": sys.argv, "testSha256": digest(Path(__file__)),
        "controlSha256": digest(CONTROL), "codecSha256": digest(TRANSPORT), "python": sys.version,
        "platform": sys.platform, "producerOnly": args.producer_only, "selectedCases": sorted(expected),
        "scope": "original byte-receipt materialization and optional selected-consumer replay"}, indent=2) + "\n")
    raise SystemExit(0 if all(row["pass"] for row in rows) else 1)


literal = b"independent selected material\r\n"
source = out / 'edited-links-input';source.mkdir()
target = source / 'target.txt'
target.write_bytes(b'initial content before the selected revision\n');target.write_bytes(literal)
(source / 'link.yaml').symlink_to('target.txt')
(source / 'absolute.yaml').symlink_to(target)
(source / 'again.yaml').symlink_to('target.txt')
files = ['target.txt', 'link.yaml', 'absolute.yaml', 'again.yaml']
expected = {name: hashlib.sha256(literal).hexdigest() for name in files}
payload = out / 'edited-links-payload'
code, details = materialize('edited-selected-links', source, payload, files, expected)
regular = code == 0 and all((payload / name).is_file() and not (payload / name).is_symlink() and
    (payload / name).read_bytes() == literal and digest(payload / name) == expected[name] for name in files)
alias_expected = sys.platform == 'win32' and sys.version_info[:3] == (3, 14, 0)
alias_observed = any(value['path']['st_birthtime_ns'] is not None and
    value['path']['st_birthtime_ns'] == value['fd']['st_birthtime_ns'] and
    value['path']['st_ctime_ns'] != value['fd']['st_ctime_ns'] for value in details.get('metadata', {}).values())
checks = {'exactRegularBytes': regular,
          'independentFiles': regular and len({((payload / name).stat().st_dev, (payload / name).stat().st_ino) for name in files}) == len(files),
          'allSelectedTransferred': code == 0 and all(not os.path.lexists(source / name) for name in files),
          'windows314AliasObserved': not alias_expected or alias_observed}
record('edited-selected-file-links', all(checks.values()), assertions=checks, exitCode=code,
       windows314AliasExpected=alias_expected, windows314AliasObserved=alias_observed, expectedSha256=expected, **details)

def reject_selected_link(fault):
    source = out / (fault + "-input");source.mkdir();(source / "target.txt").write_bytes(literal);(source / "link.yaml").symlink_to("target.txt")
    code, details = materialize(fault, source, out / (fault + "-payload"), ["link.yaml"], {"link.yaml": hashlib.sha256(literal).hexdigest()}, fault)
    if fault == 'dangling':
        boundary = (details.get('errorType') == 'FileNotFoundError' and
            Path(details.get('errorFilename') or '') == source / 'link.yaml' and not os.path.lexists(source / 'target.txt'))
    else:
        reason = {'retarget': 'selected file link changed before materialization',
                  'nonregular': 'selected file link no longer refers to a regular file',
                  'changed-during-read': 'selected file link changed while copying'}[fault]
        boundary = details.get('errorType') == 'ValueError' and details.get('error') == reason
    checks = {'refused': code == 1, 'selectedLinkRetained': (source / 'link.yaml').is_symlink(),
              'actualFaultInjected': details.get('injectedFault') is True, 'namedBoundary': boundary}
    if fault == 'changed-during-read':
        checks['actualReadMutation'] = details.get('observedReadMutation') is True
    record("selected-link-" + fault, all(checks.values()), assertions=checks, exitCode=code, **details)

for fault in ('dangling', 'retarget', 'changed-during-read'):
    reject_selected_link(fault)
portable_cases = {'edited-selected-file-links', 'selected-link-dangling', 'selected-link-retarget', 'selected-link-changed-during-read'}
if args.producer_only:
    finish(portable_cases)

source = out / "positive-input";source.mkdir()
(source / "target.txt").write_bytes(literal)
(source / "link.yaml").symlink_to("target.txt")
(source / "absolute.yaml").symlink_to(source / "target.txt")
(source / "again.yaml").symlink_to("target.txt")
# These deliberate negative fixtures retain their pre-existing unselected role.
(source / "directory-link").symlink_to(source, target_is_directory=True)
os.mkfifo(source / "fifo")
files = ["target.txt", "link.yaml", "absolute.yaml", "again.yaml"]
expected = {name: hashlib.sha256(literal).hexdigest() for name in files}
receipt_dir = out / "positive-receipt";receipt_dir.mkdir()
payload = receipt_dir / "payload-native-linux-amd64"
code, details = materialize("positive", source, payload, files, expected)
regular = code == 0 and all((payload / name).is_file() and not (payload / name).is_symlink() and (payload / name).read_bytes() == literal for name in files)
record("relative-absolute-multiple-selected-links", regular, exitCode=code, **details)
record("original-unselected-types-retained", (source / "directory-link").is_symlink() and (source / "fifo").exists())
if regular:
    identities = [(path.stat().st_dev, path.stat().st_ino) for path in (payload / name for name in files)]
    record("materialized-files-independent", len(set(identities)) == len(files))
    receipt = receipt_dir / "native-linux-amd64.json"
    receipt.write_bytes((json.dumps({"schemaVersion": 1, "lane": "native-linux-amd64", "evidence": expected,
        "pass": False, "note": "transport fixture; not a complete product lane"}, indent=3) + "\r\n").encode())
    before = {path.relative_to(receipt_dir).as_posix(): digest(path) for path in receipt_dir.rglob("*") if path.is_file()}
    occupied = out / "occupied.zip";occupied.write_bytes(b"do not replace")
    refused = codec("failed-pack", "pack", "--receipt", receipt, "--output", occupied)
    record("moved-raw-payload-survives-pack-failure", refused == 1 and occupied.read_bytes() == b"do not replace" and
        before == {path.relative_to(receipt_dir).as_posix(): digest(path) for path in receipt_dir.rglob("*") if path.is_file()})
    archive = out / "positive.zip";restored = out / "positive-restored"
    success = codec("positive-pack", "pack", "--receipt", receipt, "--output", archive) == 0 and codec("positive-hydrate", "hydrate", "--archive", archive, "--output", restored) == 0
    record("materialized-pack-hydrate-bytes", success and before == {path.relative_to(restored).as_posix(): digest(path) for path in restored.rglob("*") if path.is_file()})
else:
    for name in ("materialized-files-independent", "moved-raw-payload-survives-pack-failure", "materialized-pack-hydrate-bytes"):
        record(name, False, prerequisite="selected symlink bytes were not materialized")

reject_selected_link('nonregular')

if args.profiles:
    assert args.binary is not None
    sys.path.insert(0, str(ROOT / "tests/ci"));import run, control
    profiles, binary = args.profiles.resolve(), args.binary.resolve()
    count = run.consumer_results("wf-render-profiles", profiles, binary)
    replay_source = out / "profiles-input";replay_source.mkdir()
    shutil.copytree(profiles, replay_source / "wf-render-profiles", symlinks=True)
    selected = [path for path in replay_source.rglob("*") if path.is_file() and not control.generated_payload_path(path.relative_to(replay_source))]
    hashes = {path.relative_to(replay_source).as_posix(): digest(path) for path in selected}
    links = [path.relative_to(replay_source).as_posix() for path in selected if path.is_symlink()]
    receipt_root = out / "profiles-receipt";receipt_root.mkdir()
    payload = receipt_root / "payload-extended-0"
    code, details = materialize("actual-profiles", replay_source, payload, list(hashes), hashes)
    regular = code == 0 and all((payload / name).is_file() and not (payload / name).is_symlink() and digest(payload / name) == expected for name, expected in hashes.items())
    record("actual-profiles-materialization", regular and count > 0 and len(links) >= 2, caseCount=count, selectedFileLinks=links, exitCode=code)
    if regular:
        receipt = receipt_root / "extended-0.json"
        receipt.write_bytes((json.dumps({"schemaVersion": 1, "lane": "extended-0", "evidence": hashes, "pass": False,
            "note": "selected consumer material replay; not a complete extended lane"}, indent=2) + "\n").encode())
        archive, restored = out / "profiles.zip", out / "profiles-restored"
        success = codec("profiles-pack", "pack", "--receipt", receipt, "--output", archive) == 0 and codec("profiles-hydrate", "hydrate", "--archive", archive, "--output", restored) == 0
        actual_count = run.consumer_results("wf-render-profiles", restored / "payload-extended-0/wf-render-profiles", binary) if success else 0
        record("actual-profiles-production-validator", success and actual_count == count, originalCases=count, restoredCases=actual_count,
               originalManifestSha256=digest(profiles / "manifest.json"), originalBinarySha256=digest(binary), scope="existing selected consumer replay, not new product execution")
    else:
        record("actual-profiles-production-validator", False, prerequisite="materialization did not produce regular evidence")

expected_cases = portable_cases | {'relative-absolute-multiple-selected-links', 'original-unselected-types-retained',
    'materialized-files-independent', 'moved-raw-payload-survives-pack-failure', 'materialized-pack-hydrate-bytes', 'selected-link-nonregular'}
if args.profiles:
    expected_cases |= {'actual-profiles-materialization', 'actual-profiles-production-validator'}
finish(expected_cases)
