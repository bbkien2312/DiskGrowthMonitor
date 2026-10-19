"""Publish only aggregate QA facts, never raw JUnit paths or machine history."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree
from dgm import __version__


def junit(path):
    root=ElementTree.parse(path).getroot();suites=list(root.iter('testsuite'))
    result={key:sum(int(suite.get(key,0)) for suite in suites) for key in ('tests','failures','errors','skipped')}
    result['seconds']=round(sum(float(suite.get('time',0)) for suite in suites),2)
    if result['tests']<=0 or result['failures'] or result['errors']:raise RuntimeError('Automated QA did not pass')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('installer',type=Path);parser.add_argument('--junit',required=True,type=Path)
    parser.add_argument('--dpi125',required=True,type=Path);parser.add_argument('--dpi150',required=True,type=Path)
    parser.add_argument('--resume',type=Path,help='Local frozen checkpoint QA; publish aggregate count only')
    args=parser.parse_args();directory=args.installer.resolve().parent
    with args.installer.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
    status=json.loads((directory/'admin-smoke-status.json').read_text(encoding='utf-8-sig'))
    if not status.get('success') or status.get('fixture_only') is not True:raise RuntimeError('Final elevated QA is not complete')
    reports={}
    for filename in ('release-test-results.json','inplace-update-test-results.json'):
        report=json.loads((directory/filename).read_text(encoding='utf-8'))
        if report.get('installer_sha256')!=sha or report.get('version')!=__version__ or report.get('fixture_only') is not True:
            raise RuntimeError('QA report is not for this exact installer: '+filename)
        if report.get('fixture_removed_after_success') is not True:
            raise RuntimeError('Successful installer QA fixtures have not been removed: '+filename)
        reports[filename]=len(report['checks'])
    result={'version':__version__,'generated_utc':datetime.now(timezone.utc).isoformat(),
            'installer':{'name':'installer.exe','bytes':args.installer.stat().st_size,'sha256':sha},
            'automated':junit(args.junit),'dpi125':junit(args.dpi125),'dpi150':junit(args.dpi150),
            'elevated_binary_checks':reports,'fixture_only':True,'qa_fixtures_removed':True,
            'scope':'Synthetic/isolated D: fixtures only; no real C: scan/cache deletion or production Task/Service changes. No raw JUnit, username or captured drive history is included.'}
    if args.resume:
        resume=json.loads(args.resume.read_text(encoding='utf-8'))
        if resume.get('version')!=__version__ or resume.get('fixture_only') is not True or not resume.get('checks'):
            raise RuntimeError('Frozen checkpoint QA is not for this version')
        result['frozen_checkpoint_checks']=len(resume['checks'])
    destination=directory/'test-summary.json';destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
