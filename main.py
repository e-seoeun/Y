"""
main.py
-------
SSA 위성 식별 파이프라인 진입점.

모든 입출력은 "현재 폴더(CWD)의 고정 파일명" 을 사용한다. 경로/날짜/사이트/
카메라 등 어떤 외부 정보도 가정하지 않으므로, simulated data / derived TLE 등
임의의 경우에도 동일하게 쓸 수 있다. 처리할 자료가 든 폴더로 이동한 뒤 실행한다.

서브커맨드:
  ftp2str    ./ftp.txt                          -> ./str.txt
  str2tle    ./site.txt ./str_p.txt ./catalog.txt -> ./str_m.txt
  run        위 두 단계를 순서대로 실행
             (run 은 str2tle 입력으로 str.txt 를 그대로 사용 — line1 만 읽음)

예:
  cd /data/case01
  python /path/to/main.py ftp2str
  python /path/to/main.py str2tle
  python /path/to/main.py run

각 단계는 단독 실행도 가능:
  python /path/to/ftp2str.py
  python /path/to/str2tle.py
"""

from __future__ import annotations

import argparse
import sys

import ftp2str
import str2tle


def cmd_ftp2str(args):
    ftp2str.run()


def cmd_str2tle(args):
    str2tle.run()


def cmd_run(args):
    out_str = ftp2str.run()
    # str2tle 는 str_p.txt(line1만) 또는 str.txt(body 포함) 어느 쪽이든 처리 가능.
    str2tle.run(str_path=out_str)


def main():
    ap = argparse.ArgumentParser(
        description="SSA satellite identification pipeline "
                    "(CWD 고정 파일명: ftp.txt → str.txt → str_m.txt).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="command")
    for name, fn, desc in [
        ("ftp2str", cmd_ftp2str, "./ftp.txt -> ./str.txt"),
        ("str2tle", cmd_str2tle, "./site.txt ./str_p.txt ./catalog.txt -> ./str_m.txt"),
        ("run",     cmd_run,     "ftp2str + str2tle"),
    ]:
        p = sub.add_parser(name, help=desc)
        p.set_defaults(func=fn)

    args = ap.parse_args()
    if not args.command:
        print("Commands: ftp2str | str2tle | run")
        cmd = input("command : ").strip()
        if cmd not in ("ftp2str", "str2tle", "run"):
            print(f"[ERROR] unknown command: {cmd}")
            sys.exit(1)
        args = ap.parse_args([cmd])

    args.func(args)


if __name__ == "__main__":
    main()