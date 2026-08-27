from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_local_venv():
    base_dir=Path(__file__).resolve().parent
    version=f'python{sys.version_info.major}.{sys.version_info.minor}'
    candidates=(base_dir/'.venv'/'Lib'/'site-packages',base_dir/'.venv'/'lib'/version/'site-packages')
    for site_packages in candidates:
        if site_packages.exists():
            path=str(site_packages)
            if path not in sys.path: sys.path.insert(0,path)
            return


try:
    from app import socketio, app
except ModuleNotFoundError as exc:
    if exc.name not in {'flask','flask_socketio','reportlab','werkzeug'}:
        raise
    _bootstrap_local_venv()
    from app import socketio, app

if __name__ == '__main__':
    socketio.run(app, host='127.0.0.1', port=5000, debug=False)
