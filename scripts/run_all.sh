#!/usr/bin/env bash
# =============================================================================
# run_all.sh
# -----------------------------------------------------------------------------
# YSPACE 자료 구조 전용 일괄 실행 스크립트.
#
# 기대 구조 (입력, 읽기 전용이어도 됨):
#   YSPACE/
#     YYYYMMDD/site_name/   ... 관측 .txt (raw FTP) + *_SUMMARY.txt
#     TLE/YYYYMMDD_HHMM_catalog.txt   ... 하루에 여러 개 (시간별)
#
# 동작:
#   입력은 YSPACE 에서 "읽기"만 하고, 결과는 쓰기 가능한 OUTBASE 아래
#   YYYYMMDD/site_name/ 에 만든다 (입력 폴더가 읽기 전용인 서버 대응).
#   각 폴더마다:
#     - 입력(.txt, SUMMARY) 을 출력 폴더에 심볼릭 링크
#     - 그날 TLE 중 "가장 이른 시간(HHMM)" catalog 를 catalog.txt 로 링크
#     - ftp2str -> str2tle 실행  (결과 str.txt, str_m.txt 가 출력 폴더에 생성)
#
# 사전 조건:
#   - ftp2str / str2tle 명령이 PATH 에 있어야 한다 (pip install -e . 등).
#   - 관측소 위치는 *_SUMMARY.txt 에서 자동으로 읽는다 (site.txt 가 있으면 우선).
#
# 사용:
#   chmod +x run_all.sh
#   ./run_all.sh                          # YSPACE=~/YSPACE, OUTBASE=~/ssa_out
#   ./run_all.sh /path/to/YSPACE /path/to/out
# =============================================================================
set -u

YSPACE="${1:-$HOME/YSPACE}"
OUTBASE="${2:-$HOME/ssa_out}"
TLEDIR="$YSPACE/TLE"

shopt -s nullglob

[[ -d "$YSPACE" ]] || { echo "[오류] YSPACE 가 없습니다: $YSPACE" >&2; exit 1; }
[[ -d "$TLEDIR" ]] || { echo "[오류] TLE 폴더가 없습니다: $TLEDIR" >&2; exit 1; }
mkdir -p "$OUTBASE" || { echo "[오류] 출력 폴더를 만들 수 없습니다: $OUTBASE" >&2; exit 1; }

echo "입력 : $YSPACE   (읽기 전용 가능)"
echo "출력 : $OUTBASE"
echo

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

  out="$OUTBASE/$date/$site"
  mkdir -p "$out" || { echo "[skip] $date/$site : 출력 폴더 생성 실패"; ((n_skip++)); continue; }

  # 입력을 출력 폴더에 링크 (원본은 읽기 전용이어도 링크 읽기는 가능)
  ln -sf "$dir"*.txt "$out"/ 2>/dev/null        # 관측 raw FTP (.txt)
  ln -sf "$dir"*SUMMARY* "$out"/ 2>/dev/null    # 확장자 없는 SUMMARY 도 포함
  ln -sf "$tle" "$out/catalog.txt"

  echo "=== $date/$site   (catalog: $(basename "$tle")) ==="
  if ( cd "$out" && ftp2str && str2tle ); then
    ((n_ok++))
  else
    echo "[실패] $date/$site"
    ((n_skip++))
  fi
done

echo "-----------------------------------------------------------------"
echo "완료: 성공 $n_ok / 건너뜀·실패 $n_skip"
echo "결과 위치: $OUTBASE/<날짜>/<사이트>/str_m.txt"
