#!/usr/bin/env python3
"""Install original formula and patent runtimes inside the Linux release only."""
import argparse
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--data-root', type=Path, default=Path('/srv/zhiyan-data'))
    args = parser.parse_args()
    if not args.prepare or sys.platform != 'linux' or platform.machine() != 'x86_64' or sys.version_info[:2] != (3, 12):
        parser.error('Use --prepare with Python 3.12 on the Linux x86_64 release.')
    for command in ('node', 'npm'):
        if not shutil.which(command):
            parser.error(f'Install the target system prerequisite {command} first.')
    node_major = int(subprocess.check_output(['node', '--version'], text=True).strip().lstrip('v').split('.')[0])
    if node_major < 22:
        parser.error('The supplied patent dependencies require Node.js 22 or newer.')
    try:
        service_user = pwd.getpwnam('zhiyan')
    except KeyError:
        parser.error('Create the zhiyan service user before preparing tools.')
    if os.geteuid() not in (0, service_user.pw_uid):
        parser.error('Run as root or zhiyan so the browser cache belongs to the service user.')
    if os.geteuid() == 0 and not shutil.which('runuser'):
        parser.error('Install the system runuser prerequisite first.')
    if not Path(service_user.pw_dir).is_dir():
        parser.error('Create a writable home directory for the zhiyan service user first.')
    writable_command = ['test', '-w', service_user.pw_dir]
    if os.geteuid() == 0:
        writable_command = ['runuser', '-u', 'zhiyan', '--', *writable_command]
    subprocess.run(writable_command, check=True)
    runtime = ROOT / 'backend/app/tools/formula_recognition/runtime'
    venv = runtime / '.venv'
    python = venv / 'bin/python'
    if not venv.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    if not python.is_file():
        parser.error('Existing formula venv is incomplete; inspect it without overwriting.')
    def pip(*arguments):
        subprocess.run([str(python), '-m', 'pip', *arguments], check=True)
    pip('install', '--no-cache-dir', 'setuptools==80.10.2', 'wheel==0.48.0')
    pip('install', '--no-cache-dir', '--index-url', 'https://download.pytorch.org/whl/cpu',
        'torch==2.14.1+cpu', 'torchvision==0.29.1+cpu')
    pip('install', '--no-cache-dir', '-r', str(runtime / 'requirements.lock.txt'))
    pip('install', '--no-cache-dir', '--no-deps', '--editable', str(runtime / 'unimernet'))
    pip('check')
    weights = runtime / 'unimernet/models/unimernet_base'
    if not weights.exists():
        restored = args.data_root / 'models/unimernet_base'
        if not (restored / 'pytorch_model.pth').is_file():
            parser.error('Restore the supplied formula weights first.')
        weights.parent.mkdir(parents=True, exist_ok=True)
        weights.symlink_to(restored, target_is_directory=True)
    tools = ROOT / 'backend/app/agents/patent_drafting/runtime/vendor/patent-disclosure-skill/tools'
    # npm ci replaces node_modules. Never delete an existing dependency tree.
    if not (tools / 'node_modules').exists():
        subprocess.run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
                       cwd=tools, env={**os.environ, 'PUPPETEER_SKIP_DOWNLOAD': 'true'}, check=True)
    browser_env = dict(os.environ)
    for key in tuple(browser_env):
        if key.startswith('PUPPETEER_'):
            browser_env.pop(key)
    browser_env['HOME'] = service_user.pw_dir
    browser_command = ['node', str(tools / 'node_modules/puppeteer/install.mjs')]
    if os.geteuid() == 0:
        browser_command = ['runuser', '-u', 'zhiyan', '--', *browser_command]
    subprocess.run(browser_command, cwd=tools, env=browser_env, check=True)
    print('Tool environments prepared inside release directories. No model/API calls.')


if __name__ == '__main__':
    main()
