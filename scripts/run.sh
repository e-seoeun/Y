#!/usr/bin/env bash
# =============================================================================
# run.sh  —  특정 날짜/사이트 하나만 ftp2str + str2tle 실행
# -----------------------------------------------------------------------------
# 입력은 읽기 전용 YSPACE 에서 읽고, 결과는 쓰기 가능한 OUTBASE 아래에 만든다.
#
# 사용:
#   scripts/run.sh <YYYYMMDD> <site_name>
#   scripts/run.sh 20260501 GoHeung
#   scripts/run.sh 20260501 GoHeung /path/to/YSPACE /path/to/out   # 경로 직접 지정
#
# 사전 조건:
#   - ftp2str / str2tle 명령이 PATH 에 있어야 한다 (pip install -e . 등).
#   - YSPACE/<date>/<site>/ 에 관측 .txt + *_SUMMARY.txt 가 있어야 한다.
#   - YSPACE/TLE/<date>_HHMM_catalog.txt 가 있어야 한다 (가장 이른 시간 자동 선택).
# =============================================================================
set -eu

if [[ $# -lt 2 ]]; then
  echo "사용법: $0 <YYYYMMDD> <site_name> [YSPACE] [OUTBASE]" >&2
  exit 2
fi

date="$1"
site="$2"
YSPACE="${3:-$HOME/YSPACE}"
OUTBASE="${4:-$HOME/ssa_out}"
TLEDIR="$YSPACE/TLE"

src="$YSPACE/$date/$site"
out="$OUTBASE/$date/$site"

[[ -d "$src" ]] || { echo "[오류] 관측 폴더가 없습니다: $src" >&2; exit 1; }

# 그날 catalog 중 가장 이른 시간 (이름 정렬상 첫 번째 = 가장 이른 HHMM)
shopt -s nullglob
cats=( "$TLEDIR/${date}"_*_catalog.txt )
if (( ${#cats[@]} == 0 )); then
  echo "[오류] TLE 없음: $TLEDIR/${date}_*_catalog.txt" >&2
  exit 1
fi
IFS=$'\n' cats=($(sort <<<"${cats[*]}")); unset IFS
tle="${cats[0]}"

mkdir -p "$out"

# 입력을 출력 폴더에 링크 (원본 읽기 전용이어도 링크 읽기는 가능)
ln -sf "$src"/*.txt "$out"/ 2>/dev/null        # 관측 raw FTP (.txt)
ln -sf "$src"/*SUMMARY* "$out"/ 2>/dev/null    # 확장자 없는 SUMMARY 도 포함
ln -sf "$tle" "$out/catalog.txt"

echo "입력   : $src   (읽기 전용 가능)"
echo "catalog: $(basename "$tle")"
echo "출력   : $out"
echo

cd "$out"
ftp2str
str2tle

echo
echo "완료 → $out/str_m.txt"
