"""Windows service bootstrap - standard library only until the monitor's packages are on the path.

Registered command line (see ``app/service_host.service_command``)::

    "<base python.exe>" -I -S "<this file>" "<site-packages>" --config-dir ... --data-dir ... --log-dir ...

``-I -S`` start Python isolated and without automatic site set-up, so only the monitor's own
site-packages (added here, including its ``.pth`` files, e.g. pywin32's) is importable.
With ``--check`` as the first option it runs the self-check instead of connecting to the service
manager - the installer uses this to prove the exact command line works before registering it.
"""

import site
import sys


def _main() -> None:
    if len(sys.argv) < 3:
        print("usage: service_boot.py <site-packages> [--check] --config-dir DIR --data-dir DIR --log-dir DIR",
              file=sys.stderr)
        raise SystemExit(2)
    site.addsitedir(sys.argv[1])
    args = sys.argv[2:]
    if args[0] == "--check":
        from hfm_dashboard.app.service_host import ServiceOptions, self_check
        raise SystemExit(self_check(ServiceOptions.parse(args[1:])))
    from hfm_dashboard.adapters.windows_service import dispatch
    dispatch(args)


if __name__ == "__main__":
    _main()
