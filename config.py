"""
config.py
---------
파이프라인 전체에서 쓰는 모든 입력/출력 경로를 한 곳에 모아둔 설정 파일.

>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
                다른 PC 에서 쓸 때는 이 파일만 수정하면 됩니다.
>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

기본 구조 (이 파일이 있는 폴더를 기준 ROOT 로 둠):

    SSA/
    ├── repository/
    │   ├── observed_data/{date}/{site}/...   ← [INPUT]  raw FTP
    │   ├── dot_tle/{date}.tle                ← [INPUT]  TLE
    │   └── interim/{date}/{site}/...         ← [중간]   str.txt
    └── results/{date}/{site}/...             ← [OUTPUT] str_m.txt  (최종 결과)
"""

from pathlib import Path

# 이 config.py 가 있는 폴더 (= 프로젝트 ROOT)
ROOT = Path(__file__).resolve().parent


# =============================================================================
# [INPUT]  Step 1 입력: 카메라별 raw FTP 관측 파일
# =============================================================================
# 기대 구조:  {OBS_ROOT}/{date}/{site}/{date}_{site}_cam{N}.txt
#   예) repository/observed_data/20260417/JangBoGo/20260417_JangBoGo_cam1.txt
#
# 다른 위치를 쓰려면:
#   OBS_ROOT = Path(r"D:\satellite_data\observed_data")        # Windows
#   OBS_ROOT = Path("/mnt/data/observed_data")                 # Linux/Mac
OBS_ROOT = ROOT / "repository" / "observed_data"


# =============================================================================
# [INPUT]  Step 2 입력: TLE 카탈로그 파일
# =============================================================================
# 기대 파일:  {TLE_ROOT}/{date}.tle
#   예) repository/dot_tle/20260417.tle
TLE_ROOT = ROOT / "repository" / "dot_tle"


# =============================================================================
# [중간]  Step 1 출력 / Step 2 입력: 1초 간격 중간 저장파일
# =============================================================================
# 생성/로드:  {INTERIM_ROOT}/{date}/{site}/{date}_{site}_str.txt
INTERIM_ROOT = ROOT / "repository" / "interim"


# =============================================================================
# [OUTPUT] Step 2 최종 결과: 식별 결과 파일
# =============================================================================
# 생성:  {RESULTS_ROOT}/{date}/{site}/{date}_{site}_str_m.txt
RESULTS_ROOT = ROOT / "results"