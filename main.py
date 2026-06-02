"""
main.py
-------
SSA 위성 식별 파이프라인 진입점.

서브커맨드:
  ftp2str    raw FTP → repository/interim/.../<date>_<site>_str.txt
  str2tle    _str.txt → results/.../<date>_<site>_str_m.txt
  run        위 두 단계를 순서대로 실행

예:
  python main.py run 20260417 J
  python main.py ftp2str 20260417 SJ
  python main.py str2tle 20260417 SJ
  python main.py                       # 대화형

site 인자는 site code (J, G, U, H, UD, Y, YS, SJ, HC) 또는 풀네임 모두 OK.

모든 입출력 경로는 config.py 에서 관리합니다.
"""

from __future__ import annotations

import argparse
import sys

from classes.sites import get_any, SITES

import ftp2str
import str2tle


def _prompt_if_missing(date_str: str | None, site_key: str | None):
    if not date_str:
        date_str = input("YYYYMMDD : ").strip()
    if not site_key:
        print("\nAvailable sites:")
        for code, s in SITES.items():
            print(f"  {code:>3s}  -  {s.name}")
        site_key = input("Site code or name: ").strip()
    return date_str, site_key


def cmd_ftp2str(args):
    date_str, site_key = _prompt_if_missing(args.date, args.site)
    site = get_any(site_key)
    ftp2str.run(date_str, site, cams=args.cams)


def cmd_str2tle(args):
    date_str, site_key = _prompt_if_missing(args.date, args.site)
    site = get_any(site_key)
    str2tle.run(date_str, site)


def cmd_run(args):
    date_str, site_key = _prompt_if_missing(args.date, args.site)
    site = get_any(site_key)
    ftp2str.run(date_str, site, cams=args.cams)
    str2tle.run(date_str, site)


def main():
    ap = argparse.ArgumentParser(
        description="SSA satellite identification pipeline (FTP → str → identification).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="command")

    for name, fn, desc in [
        ("ftp2str", cmd_ftp2str, "FTP raw  ->  str.txt   (Step 1)"),
        ("str2tle", cmd_str2tle, "str.txt  ->  str_m.txt (Step 2: identification)"),
        ("run",     cmd_run,     "ftp2str + str2tle"),
    ]:
        p = sub.add_parser(name, help=desc)
        p.add_argument("date", nargs="?", help="YYYYMMDD")
        p.add_argument("site", nargs="?", help="Site code or name")
        p.add_argument("--cams", nargs="*",
                       help="cam IDs (ftp2str / run only; omit to auto-detect)")
        p.set_defaults(func=fn)

    args = ap.parse_args()

    if not args.command:
        # interactive
        print("Commands: ftp2str | str2tle | run")
        cmd = input("command : ").strip()
        if cmd not in ("ftp2str", "str2tle", "run"):
            print(f"[ERROR] unknown command: {cmd}")
            sys.exit(1)
        sys.argv = [sys.argv[0], cmd]
        args = ap.parse_args([cmd])

    args.func(args)


if __name__ == "__main__":
    main()