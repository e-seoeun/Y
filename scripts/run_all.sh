#!/usr/bin/env bash
# =============================================================================
# run_all.sh
# -----------------------------------------------------------------------------
# YSPACE 자료 구조 전용 일괄 실행 스크립트.
#
# 기대 구조:
#   YSPACE/
#     YYYYMMDD/site_name/   ... 관측 .txt (raw FTP)
#     TLE/YYYYMMDD_HHMM_catalog.txt   ... 하루에 여러 개 (시간별)
#
# 동작:
#   각 (날짜/사이트) 폴더마다, 그날 TLE 중 "가장 이른 시간(HHMM)" catalog 를
#   catalog.txt 로 심볼릭 링크한 뒤 ftp2str -> str2tle 를 실행한다.
#
#   - 관측소 위치: 각 폴더의 *_SUMMARY.txt 에서 자동으로 읽는다 (site.txt 가 있으면 우선).
#   - ftp2str / str2tle 명령이 PATH 에 있어야 한다 (pip install -e . 또는 --user).
#     설치를 안 했다면 아래 FTP2STR / STR2TLE 를 'python3 /경로/ftp2str.py' 식으로 바꾼다.
#
# 사용:
#   chmod +x run_all.sh
#   ./run_all.sh                  # 스크립트가 있는 폴더를 YSPACE 로 간주
#   ./run_all.sh /path/to/YSPACE  # 또는 YSPACE 경로를 직접 지정
# =============================================================================
set -u

YSPACE="${1:-$(cd "$(dirname "$0")" && pwd)}"
TLEDIR="$YSPACE/TLE"

# 실행 명령 (설치 안 했으면 'python3 /경로/SSA/ftp2str.py' 등으로 교체)
FTP2STR="ftp2str"
STR2TLE="str2tle"

shopt -s nullglob

if [[ ! -d "$TLEDIR" ]]; then
  echo "[오류] TLE 폴더가 없습니다: $TLEDIR" >&2
  exit 1
fi

n_ok=0
n_skip=0
for dir in "$YSPACE"/*/*/ ; do
  site=$(basename "$dir")
  date=$(basename "$(dirname "$dir")")
  [[ "$date" =~ ^[0-9]{8}$ ]] || continue          # YYYYMMDD 형식 폴더만

  # 그날 catalog 중 가장 이른 시간 (이름 정렬상 첫 번째 = 가장 이른 HHMM)
  cats=( "$TLEDIR/${date}"_*_catalog.txt )
  if (( ${#cats[@]} == 0 )); then
    echo "[skip] $date/$site : TLE 없음 ($TLEDIR/${date}_*_catalog.txt)"
    ((n_skip++)); continue
  fi
  IFS=$'\n' cats=($(sort <<<"${cats[*]}")); unset IFS
  tle="${cats[0]}"

  # 관측소 위치는 str2tle 가 site.txt 또는 *_SUMMARY.txt 에서 자동으로 읽는다.
  echo "=== $date/$site   (catalog: $(basename "$tle")) ==="
  if ( cd "$dir" && ln -sf "$tle" catalog.txt && "$FTP2STR" && "$STR2TLE" ); then
    ((n_ok++))
  else
    echo "[실패] $date/$site"
    ((n_skip++))
  fi
done

echo "-----------------------------------------------------------------"
echo "완료: 성공 $n_ok / 건너뜀·실패 $n_skip"