# EasyEDA Pro Schematic Analyzer

EasyEDA Pro 회로도 프로젝트 파일(`.eprj`)을 파싱하여 BOM, 넷 연결, IC 핀맵, 분압기 계산 등을 자동 추출하는 Python 스크립트입니다.

EasyEDA Pro에서 회로도를 설계한 뒤, **회로가 의도한 대로 연결되어 있는지를 프로그래밍적으로 검증**할 수 있습니다. 사람이 눈으로 확인하기 어려운 멀티시트 간 넷 공유, floating 부품, 분압비 오류 등을 자동으로 감지합니다.

## 왜 이 스크립트가 필요한가? — 토큰 절약과 MCP 패턴

EasyEDA Pro의 `.eprj` 파일은 SQLite 내부에 base64+gzip 압축된 JSON-lines로 저장됩니다. 시트 하나의 원본 데이터만 해도 **수천 줄, 수만 토큰**에 달합니다. Claude AI가 이 원본을 직접 받아 분석하면 토큰이 빠르게 소진되어 세션이 금방 만료됩니다.

이 스크립트는 **MCP(Model Context Protocol)와 동일한 패턴**으로 동작합니다:

```
┌─────────────────────────────────────────────────────┐
│  기존 방식 (스크립트 없이)                              │
│                                                     │
│  .eprj (SQLite) → 디코딩 → 수만 줄 JSON 원본         │
│       → Claude가 전부 수신 (토큰 대량 소비)              │
│       → 분석 시도 중 세션 만료                          │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  이 스크립트 사용 시 (MCP 패턴)                         │
│                                                     │
│  .eprj (SQLite) → Python이 로컬에서 전처리             │
│       ├─ Union-Find 넷 트레이싱                       │
│       ├─ 핀 좌표 변환 (회전/미러/x-negated fallback)    │
│       ├─ MPN 자동 디코딩 (저항값 해석)                   │
│       └─ 분압기 자동 계산                              │
│       → ~50줄 구조화된 요약 출력                        │
│       → Claude는 결과 해석과 설계 검증에만 집중            │
│       → 토큰 사용량 대폭 절감                           │
└─────────────────────────────────────────────────────┘
```

| 항목 | 원본 직접 전달 | 스크립트 사용 |
|------|---------------|-------------|
| 토큰 소비 | 시트당 수만 토큰 | ~50줄 요약 |
| 연산 부담 | Claude가 JSON 파싱 + 좌표 계산 | Python이 로컬 처리 |
| 정확도 | 좌표 기반 추정 (오류 가능) | Union-Find 기반 정확한 넷 추적 |
| 세션 지속 | 1-2회 분석으로 만료 | 반복 분석 가능 |

MCP 서버가 외부 도구를 호출하고 결과만 LLM에 전달하는 것처럼, 이 스크립트도 **무거운 데이터 처리는 로컬 Python이 담당하고, Claude는 구조화된 결과의 해석과 설계 판단에 집중**하는 구조입니다.

## 주요 기능

- **BOM 추출** — Designator, MPN, 풋프린트, 위치/회전 포함
- **저항 MPN 자동 디코딩** — `0603WAF1803T5E` → `180kΩ` 등
- **넷 트레이싱** — Union-Find 기반 전기적 연결 완전 추적
- **Cross-sheet 넷 비교** — 멀티시트 간 공유/전용 넷 분류
- **Floating 부품 감지** — 배선 누락 자동 검출
- **FB 분압기 자동 계산** — BQ24650, TPS61088, LGS5145 등
- **IC 신호넷 요약** — IC별 연결 부품 한눈에 파악
- **IC 핀맵** — 실제 배치된 IC만 핀 번호→이름 매핑

## 빠른 시작

### 요구 사항

- Python 3.8+
- 외부 라이브러리 없음 (표준 라이브러리만 사용)

### 실행

```bash
# 요약 모드 (권장)
python3 analyze_eprj.py your_project.eprj --summary

# 상세 모드
python3 analyze_eprj.py your_project.eprj

# JSON 출력
python3 analyze_eprj.py your_project.eprj --json

# 특정 시트만
python3 analyze_eprj.py your_project.eprj --summary --sheet power
```

### 출력 예시 (--summary)

```
Project: solar_aton_main_v100  |  Sheets: 2
────────────────────────────────────────────────────────────
  Power: 64 parts (IC:6 R:21 C:24) | 12 nets | 31 pwr syms
  MCU: 39 parts (IC:2 R:10 C:9) | 23 nets | 22 pwr syms

────────────────────────────────────────────────────────────
Cross-sheet nets:
  Shared (8): 3V3, ADC_NTC, BAT_CUR, BAT_VOL, CHG_EN, GND, SOL_CUR, SOL_VOL
  power-only (4): +BV, +PV, BAT+, VREF_1_6V
  mcu-only (15): BZ_PWM, EN, OLED_SCL, ...

────────────────────────────────────────────────────────────
Floating components (no net connections):
  ⚠ C8       (0uF) on power

────────────────────────────────────────────────────────────
FB voltage dividers:
  U4 (BQ24650): R64=140kΩ / R65=50kΩ  → Vout=2.09×(1+140k/50.0k)=7.94V
  U9 (LGS5145): R4=33kΩ / R9=10.7kΩ  → Vout=0.8×(1+33k/10.7k)=3.27V

────────────────────────────────────────────────────────────
IC signal-net connections:
  U1     (ESP32-C6-MINI-1-N4  ) [mcu]  pwr:3V3,GND
         BZ_PWM: Q7
         EN: C40, R18, SW5
         OLED_SCL: OLED1
         ...
────────────────────────────────────────────────────────────
```

---

## .eprj 파일 구조

EasyEDA Pro의 `.eprj` 파일은 **SQLite 3 데이터베이스**입니다. 회로도 데이터는 `documents` 테이블의 `dataStr` 컬럼에 **base64 인코딩 + gzip 압축된 JSON-lines** 형식으로 저장됩니다.

### DB 테이블

| 테이블 | 내용 |
|--------|------|
| `projects` | 프로젝트 이름, 메타데이터 (1행) |
| `documents` | 회로도 시트(docType=1), PCB(docType=3) |
| `components` | 심볼(docType=2), 풋프린트(docType=4), 파워심볼(docType=18) |
| `devices` | 심볼-풋프린트 연결 정의 |
| `attributes` | 디바이스별 속성 (key-value) |

### dataStr 디코딩

```python
import base64, zlib
raw = base64.b64decode(dataStr.removeprefix("base64"))
text = zlib.decompress(raw, 15 + 32).decode("utf-8")
```

디코딩된 텍스트는 한 줄에 하나씩 JSON 배열입니다:

```json
["COMPONENT", "e1488", "DTC143ZE.1", 755, 675, 0, 0, {}, 0]
["ATTR", "e1490", "e1488", "Designator", "Q7"]
["WIRE", "e1523", [[705,675,655,675]], "style", 0]
["ATTR", "e1524", "e1523", "NET", "BZ_PWM"]
```

### 주요 요소 타입

| 타입 | 설명 | 형식 |
|------|------|------|
| `COMPONENT` | 부품 배치 | `[type, id, name, x, y, rot, mirror, {}, 0]` |
| `ATTR` | 속성 | `[type, id, parent_id, key, value, ...]` |
| `WIRE` | 전기 배선 | `[type, id, [[x1,y1,x2,y2], ...], style, 0]` |
| `PIN` | 심볼 핀 정의 | `[type, id, 1, null, x, y, length, rot, ...]` |
| `TEXT` | 텍스트 | `[type, id, x, y, rot, text, style, 0]` |

---

## 동작 원리 상세

### 1. BOM 추출

`COMPONENT` 요소 중 `Designator` 속성이 있으면 BOM 항목, 없으면 파워 심볼로 분류합니다.

저항 MPN은 `decode_r()` 함수로 자동 디코딩합니다:

| MPN 패턴 | 결과 | 규칙 |
|-----------|------|------|
| `0603WAF1803T5E` | 180kΩ | 4자리 코드: 180×10³ |
| `0603WAF1002T5E` | 10kΩ | 100×10² |
| `0805W8F200KT5E` | 200kΩ | K 단위문자 직접 |
| `RC0603FR-07100KL` | 100kΩ | `07` 뒤 값+단위 |
| `RT0603BRD0750KL` | 50kΩ | 동일 구조 |
| `MFJ06HR010FT` | 10mΩ | mΩ 센스 저항 |
| `1206W4F200LT5E` | 200mΩ | L = mΩ |

### 2. 넷 트레이싱 (Union-Find)

Union-Find(Disjoint Set) 자료구조로 모든 전기적 연결을 추적합니다. 4단계로 연결을 구축합니다:

**① 동일 WIRE ID** — 같은 와이어의 다중 세그먼트는 동일 넷

```
WIRE e100: seg1(0,0→100,0), seg2(100,0→100,50)
→ (0,0), (100,0), (100,50) 모두 같은 넷
```

**② 좌표 공유** — 서로 다른 와이어라도 끝점 좌표가 같으면 연결

```
WIRE e100: (50,50)→(100,50)
WIRE e200: (100,50)→(150,50)
→ (100,50) 공유 → 같은 넷
```

**③ T-접합** — 와이어 끝점이 다른 와이어 선분의 중간에 있으면 연결

```
WIRE e100: (0,50)→(200,50)   ← 수평선
WIRE e200: (100,0)→(100,50)  ← (100,50)이 e100 위에 있음
→ T-접합 → 같은 넷
```

**④ 동일 NET 이름** (핵심) — WIRE의 `NET` 속성이 같으면 물리적 거리와 무관하게 연결

```
WIRE e100 NET="BZ_PWM": (705,675)→(655,675)  ← MCU 시트
WIRE e200 NET="BZ_PWM": (500,450)→(435,450)  ← 같은 시트 다른 위치
→ NET 이름 동일 → 같은 넷
```

이것이 EasyEDA Pro의 **주요 넷 연결 메커니즘**입니다. 화면에서 와이어가 떨어져 있어도 같은 NET 이름이면 전기적으로 연결됩니다.

### 3. 파워 심볼 넷 이름 해석

파워 심볼의 넷 이름은 심볼 라이브러리 제목이 아니라, 연결된 와이어의 `NET` 속성에서 가져옵니다.

```
COMPONENT: name="24v" (라이브러리 제목)
→ 연결된 WIRE의 NET 속성 = "+12V" (실제 넷 이름)
→ "+12V" 사용 ✅
```

이 처리가 없으면 라이브러리 심볼 제목과 실제 넷 이름이 불일치하는 경우가 생깁니다.

### 4. 핀 좌표 계산

각 부품의 심볼 핀 좌표를 절대 좌표로 변환합니다:

```
절대좌표 = 부품위치 + rotate(핀오프셋, 부품회전, 부품미러)
```

회전 변환:

| 각도 | 변환 |
|------|------|
| 0° | (x, y) → (x, y) |
| 90° | (x, y) → (-y, x) |
| 180° | (x, y) → (-x, -y) |
| 270° | (x, y) → (y, -x) |

미러(mirror=1)일 때는 회전 전에 x를 부호 반전합니다.

**x좌표 반전 fallback**: 일부 심볼(DTC143ZE 등)에서 핀 x좌표가 실제 와이어 연결점과 부호가 반대인 경우가 있습니다. 이를 처리하기 위해 정상 좌표와 x-반전 좌표를 모두 후보로 등록하고, 와이어에 실제로 닿는 쪽을 채택합니다.

**Multi-part suffix**: 컴포넌트 이름이 `DTC143ZE.1`처럼 `.N` suffix가 붙은 경우, suffix를 제거하고 라이브러리의 `dtc143ze` 심볼과 매칭합니다.

### 5. IC pin maps 필터링

컴포넌트 라이브러리(`components` 테이블)에는 회로도에 배치하지 않은 심볼도 남아있을 수 있습니다 (다른 프로젝트에서 복사 시 딸려옴). IC pin maps는 **실제 회로도 BOM에 배치된 컴포넌트의 심볼만** 출력합니다.

### 6. FB 분압기 자동 계산

등록된 IC의 FB 핀에 연결된 저항 분압기를 자동 감지합니다.

| IC | Vref | FB 핀 |
|----|------|-------|
| BQ24650 | 2.09V | VFB |
| TPS61088 | 0.6V | FB |
| LGS5145 | 0.8V | FB |

감지 조건:
1. IC에 직접 연결된 저항 2개가 서로 연결됨
2. 한쪽이 GND에 연결 (bottom)
3. 다른 쪽은 GND 미연결 (top)

계산: `Vout = Vref × (1 + R_top / R_bot)`

---

## Claude AI 스킬 연동

이 스크립트는 **Claude AI의 사용자 스킬(User Skill)** 로 등록하여 사용할 수 있습니다. 스킬로 등록하면 `.eprj` 파일을 업로드하거나 회로도 관련 질문을 할 때 Claude가 자동으로 스크립트를 실행합니다.

### 스킬 트리거 조건

- `.eprj` 파일 업로드
- "회로도 분석", "EDA 파일", "schematic analysis" 언급
- EasyEDA Pro 회로도 리뷰 요청
- BOM/넷리스트 추출 요청
- 회로 설계 스펙 대비 검증 요청

### 스킬 디렉토리 구조

```
easyeda-pro-analyzer/
├── SKILL.md          ← 스킬 메타데이터 + Claude 가이드
├── analyze_eprj.py   ← 분석 스크립트
└── README.md         ← 이 문서
```

### SKILL.md 역할

`SKILL.md`는 Claude가 분석 결과를 해석할 때 참조하는 가이드입니다. 핵심 지침:

1. **좌표 근접성으로 부품-IC 매핑 금지** — 반드시 넷 연결 데이터 기반으로 판단
2. **파워 심볼 넷 이름은 라이브러리 제목이 아닌 와이어 NET 속성** 사용
3. 분석 결과 제시 시 IC 블록별로 그룹화하고 분압기/센스저항/바이패스캡 용도를 함께 표시
4. 설계 사양과 다른 값이 있으면 플래그

### 스킬 등록 방법

Claude 프로젝트의 스킬 디렉토리에 파일을 배치합니다:

```
/mnt/skills/user/easyeda-pro-analyzer/
├── SKILL.md
└── scripts/
    └── analyze_eprj.py
```

---

## 알려진 제한 사항

| 항목 | 설명 |
|------|------|
| 커넥터 핀 매핑 | B2P-VH-BL 등 일부 커넥터는 핀 좌표 매칭 실패로 floating 오탐 |
| 디커플링 캡 | 파워 심볼 직접 연결 캡이 좌표 오차로 floating 오탐 가능 |
| 마운팅 홀 | 전기적 넷이 없으므로 항상 floating (정상) |
| FB 분압기 오탐 | ADC 분압 저항도 IC+GND 경로 공유 시 FB 분압기로 오탐 가능 |
| 핀 x좌표 반전 | 일부 심볼에서 발생, x-negated fallback으로 대부분 해결 |
| PCB 미지원 | 회로도(docType=1)만 분석. PCB 레이아웃(docType=3) 미지원 |
| 좌표 스냅 | 0.5 단위 snap 처리로 미세 좌표 불일치 시 연결 누락 가능 |

---

## 변경 이력

| 버전 | 날짜 | 변경 |
|------|------|------|
| v4.1 | 2026-03-31 | `.N` multi-part suffix 매칭, x-negated 핀 좌표 fallback, IC pin maps 배치 부품만 필터링 |
| v4 | 2026-03-30 | `--summary` 모드, `decode_r()` 버그 3건 수정, NET 속성 기반 넷 해석 |

---

## 라이선스

MIT License
