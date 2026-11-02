"""Run Studio: `python -m studio [--host 127.0.0.1] [--port 8020] [--desktop]`.

With `--port 0` (or when the preferred port is busy and `--any-port` is given) a free port is chosen, and
the first line printed is `REELWRIGHT_PORT <n>` so the desktop supervisor knows where to connect."""
import argparse
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m studio')
    parser.add_argument('--host', default='127.0.0.1', help='0.0.0.0 lets phones and other PCs connect')
    parser.add_argument('--port', type=int, default=8020)
    parser.add_argument('--any-port', action='store_true', help='use a free port if the chosen one is taken')
    parser.add_argument('--desktop', action='store_true', help='per-user folders instead of the checkout')
    args = parser.parse_args(argv)
    if args.desktop:
        os.environ['REELWRIGHT_DESKTOP'] = '1'
    from . import paths  # after REELWRIGHT_DESKTOP is set
    port = args.port
    if port == 0 or (args.any_port and not paths.port_free(port, args.host)):
        port = paths.free_port(0 if port == 0 else port, args.host)
    print(f'REELWRIGHT_PORT {port}', flush=True)
    import uvicorn
    uvicorn.run('studio.app:app', host=args.host, port=port, log_level='info')


if __name__ == '__main__':
    sys.exit(main())
