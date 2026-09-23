"""Run offline checks with the bundled Python: runtime/python.exe -B check.py."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
CHECKS = ('reading_check', 'model_selection_check', 'language_packages_check',
          'package_removal_check', 'language_ui_check', 'twitch_ads_source_check',
          'integrated_check', 'transitions_check', 'twitch_ads_playback_check',
          'runtime_check', 'source_playback_check')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--one', choices=CHECKS)
    args = parser.parse_args()
    (ROOT/'outputs').mkdir(exist_ok=True)
    (ROOT/'data').mkdir(exist_ok=True)
    if args.one:
        sys.path.insert(0, str(ROOT/'tests'))
        sys.argv = [str(ROOT/'tests'/f'{args.one}.py')]
        runpy.run_path(sys.argv[0], run_name='__main__')
        return 0
    commands = [('unit', ['-m', 'unittest', 'discover', '-s', str(ROOT/'tests'), '-p', 'test_*.py'])]
    commands += [(name, [str(Path(__file__).resolve()), '--one', name]) for name in CHECKS]
    results = []
    for name, command in commands:
        print('Checking '+name, flush=True)
        result = subprocess.run([sys.executable, '-B', *command], cwd=ROOT, timeout=240)
        results.append(dict(name=name, passed=result.returncode == 0))
        if result.returncode:
            break
    (ROOT/'outputs/checks.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    return 0 if all(item['passed'] for item in results) else 1

if __name__ == '__main__':
    raise SystemExit(main())
