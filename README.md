# SSA Satellite Identification Pipeline

관측 streak (raw FTP) 을 받아 TLE/SGP4 기반으로 위성을 식별하는 파이프라인.

## 단계

```
repository/observed_data/{date}/{site}/{date}_{site}_cam{N}.txt   # raw FTP (입력)
                            │
                            ▼  ftp2str.py
repository/interim/{date}/{site}/{date}_{site}_str.txt            # 1초 grid 통합본
                            │
                            ▼  str2tle.py  (+ repository/dot_tle/{date}.tle)
results/{date}/{site}/{date}_{site}_str_m.txt                     # 식별 결과 (최종)
```

- **Step 1 (`ftp2str`)** : 모든 카메라의 FTP 파일을 한 사이트/날짜 단위로 통합. center 기준 ±1초 대칭 1초 grid 로 LS resampling.
- **Step 2 (`str2tle`)** : TLE 후보군 SGP4 전파 → angular distance / PA / 각속도 / D1·D2 기반 stage-1 / stage-2 평가 → 최적 매칭 1개.

## 설치

```bash
pip install -r requirements.txt
```

Python 3.9+ 권장. 의존성:
- `numpy`, `pandas`, `astropy`, `sgp4`

## 디렉토리 구조

```
SSA/
├── main.py                 # CLI 진입점
├── config.py               # 모든 경로 설정 (다른 PC 에서 여기만 수정)
├── ftp2str.py              # Step 1: FTP -> str.txt
├── str2tle.py              # Step 2: str.txt -> str_m.txt
├── candidate_search.py     # TLE/SGP4 후보 탐색
├── motion.py               # angular motion (관측 / 후보)
├── evaluate.py             # D1/D2 + stage1/stage2 평가
├── classes/
│   └── sites.py            # site / camera 카탈로그
├── repository/
│   ├── observed_data/      # 입력 raw FTP
│   ├── dot_tle/            # 입력 TLE  ({date}.tle)
│   └── interim/            # str.txt (중간)
└── results/                # 최종 식별 결과 (str_m.txt)
```

## 사용법

```bash
# 전체 파이프라인
python main.py run 20260417 J
python main.py run 20260417 SJ

# 단계별
python main.py ftp2str 20260417 J
python main.py str2tle 20260417 J

# 특정 카메라만
python main.py ftp2str 20260417 SJ --cams 101 102

# 인자 없이 대화형
python main.py
```

### 지원 사이트 (`classes/sites.py`)

| code | name           | cameras |
|------|----------------|---------|
| J    | JangBoGo       | 1–7     |
| G    | GoHeung        | 1–5     |
| U    | Ulsan          | 1–5     |
| H    | HaeAn          | 1–5     |
| HC   | Hwacheon       | 1–7     |
| UD   | Uniduck        | 1–5     |
| Y    | YoungYang      | 1–7     |
| YS   | YoungYang_SSA  | 101–111 |
| SJ   | SSA_JangBogo   | 101–105 |

## 결과 파일 포맷 (`_str_m.txt`)

```
# streak_name   N  time                      RA_center  Dec_center  MotionAng     Speed  NORAD   d1   d2   da   dv
FF_AQ0001_..._0001  9  2026-04-17T06:38:50.277  102.4731  -26.2316  173.6186  0.361750  58507  ...
...
FF_AQ0001_..._0001  7  2026-04-17T08:22:00.962  175.3890  -32.0938  331.4283  0.478836  no_match  -   -   -   -
```

컬럼:
- `NORAD` : 매칭된 위성번호 또는 `no_match`
- `d1`, `d2` : observation vs candidate 선분의 normal / parallel 거리 (deg)
- `da` : position angle 차이 (deg)
- `dv` : 각속도 상대오차 ((cand - obs) / obs)

매칭 실패 시 d1~dv 는 `-`.

## 새 사이트/카메라 추가

`classes/sites.py` 의 `SITES` 딕셔너리에 `Site(...)` 한 줄 추가.

## 다른 PC로 옮길 때

- **모든 입출력 경로는 `config.py` 한 파일에 모여 있음.** 다른 위치를 쓰려면 거기만 수정.
- 기본 구조 그대로면 `repository/observed_data/{date}/{site}/...` 와 `repository/dot_tle/{date}.tle` 만 채우면 됨.
- 예: 외장 드라이브 사용
  ```python
  # config.py
  OBS_ROOT     = Path(r"D:\satellite_data\observed_data")
  TLE_ROOT     = Path(r"D:\satellite_data\dot_tle")
  INTERIM_ROOT = Path(r"D:\satellite_data\interim")
  RESULTS_ROOT = Path(r"D:\satellite_data\results")
  ```