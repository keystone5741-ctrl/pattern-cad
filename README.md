# pattern-cad

의류 패턴 CAD. 2D 제도 · 그레이딩 · 마카까지 한 프로그램에서 하되,
**의류 카테고리 · 핏 · 디테일을 고르면 기본원형이 자동으로 생성**되어 제도 시간을 크게 줄이는 것이 목표다.

## 지금 단계 — 제도법 데이터화

자동 생성의 재료는 제도 공식이다. 교과서 공식이 아니라 실제로 쓰는 제도법을 데이터로 만들기 위해,
포트폴리오(`reference/portfolio.pdf`, 일러스트레이터 벡터 PDF 100p)에서 패턴 선·치수·유의사항을
아이템별로 분리해 두었다.

```
reference/portfolio.pdf        원본 (스커트 11 · 팬츠 8 · 상의 12 + 원형 2 · 자켓 9)
tools/extract_portfolio.py     추출 도구
extracted/
  index.md / index.json        아이템 목록 + 사이즈표 요약
  <카테고리>/<번호_아이템>/
    pNNN.svg                   페이지 전체. 층(layer)별 그룹: 패턴 제도 / 전개 / 그레이딩 / 마카 / 도식화 / 유의사항 / 장식
    pNNN_pattern.svg           패턴 선 + 치수 글자만
    pNNN_pattern.png           위 SVG 미리보기
    size.json                  신체 사이즈 / 패턴 사이즈
    annotations.json           도면 위 치수·공식 글자와 위치 (인치 값 파싱 포함)
    notes.md                   패턴 제도 유의사항 본문
```

- 단위는 인치. 이 포트폴리오 표기 `3.1/2”` 는 3½″, `1/8”` 는 ⅛″ 다. `annotations.json` 의 `inch` 필드에 소수로 풀어 두었다.
- SVG 좌표는 PDF 포인트(pt) 그대로이고 도면은 실물 축척이 아니다. 실제 치수는 좌표가 아니라 글자(치수 주석)에서 읽는다.
- 층으로 나뉜 SVG는 일러스트레이터·잉크스케이프에서 그룹별로 켜고 끌 수 있다.

다시 뽑으려면:

```
pip install -r requirements.txt
python tools/extract_portfolio.py              # 전부
python tools/extract_portfolio.py --pages 4 44 # 일부만
```

## 규칙 엔진과 원형 (1단계)

점과 선에 이름과 규칙을 붙여 저장한다. 좌표는 치수에서 계산된다.

```
patterncad/
  units.py      인치 분수 표기 (3.1/2 = 3½)
  geometry.py   점 · 직선 · 3차 베지어, 교점 · 수선 · 곡선 맞춤
  expr.py       치수 식 ("B/4 + 여유/4", "dist(SNP_B, SP_B)", "SP_F.y")
  block.py      원형 YAML 읽기 → 점·선 계산
  svg.py        계산 결과를 SVG로
blocks/
  sichuni_basic.yaml        시추니 기본 원형 (치수 → 점 규칙 → 선)
  sichuni_dartless.yaml     시추니 무다트 원형 — 기본 원형을 상속(extends)해 바뀐 값만
  top_body.yaml             상의 몸판 기본틀 — 원형에 허리·엉덩이·밑단 틀을 붙인 것.
                            옆선을 앞·뒤 따로 잡아 패턴가슴/허리/엉덩이 를 식으로 확인한다
  box_body.yaml             무다트 몸판틀 — 니트·오버사이즈 아이템의 부모
  sleeve_basic.yaml         소매 원형 — 앞·뒤 암홀 길이에서 소매산높이(AH/3 + 조정)와 사선을 계산. 소매산 단계 선택
  shirt_collar_blouse_body.yaml  셔츠 칼라 블라우스 몸판 (기본 원형 상속)
  shirt_collar_band.yaml / shirt_collar.yaml / cuff.yaml / sleeve_placket.yaml  부속: 칼라밴드·칼라·커프스·견보루
  oversize_blouse_body.yaml / china_collar_blouse_body.yaml / pussy_bow_blouse_body.yaml
  sweat_shirt_body.yaml / oversize_hoodie_body.yaml / tight_t_shirt_body.yaml
  sleeveless_dress_body.yaml / h_line_dress_body.yaml / mermaid_dress_body.yaml
  flat_collar_dress_body.yaml / jump_suite_body.yaml / jump_suite_pants.yaml
  jacket_body.yaml          자켓 몸판 기본틀 — 낸단·라펠·고지·사이바·후다·미까시
  hourglass_jacket_body.yaml / half_double_jacket_body.yaml / one_button_jacket_body.yaml
  shawl_collar_jacket_body.yaml / stand_collar_jacket_body.yaml / hunting_jacket_body.yaml
  rider_jacket_body.yaml / oversized_jacket_body.yaml
  tailored_collar.yaml / shawl_collar.yaml / stand_collar.yaml   자켓 칼라
  sleeve_tuck.yaml          턱(셔링) 소매 — 앞 1"·뒤 1.1/4" 로 턱 위치를 다르게
  sleeve_two_piece.yaml     두 장 소매 — 큰소매 + 작은소매 (합이 소매통)
  hood.yaml / rib.yaml / bow_tie.yaml / flat_collar.yaml / banana_band.yaml  부속
  skirt_basic.yaml          제허리 스커트 원형 — H/4 ∓ 1/4 폭, W/4 + 이세, 다트 분량 자동 배분, 겹트임
  skirt_hipbone.yaml        힙본 스커트 원형 (제허리 원형 상속)
  skirt_aline.yaml          세미A / A라인 스커트 — 힙본 원형을 절개해 밑단으로 벌림 (첫 기하 조작)
  skirt_tapered.yaml        테이퍼드 스커트 — 반대로 밑단을 축으로 벌려 허리에 턱, 밑단은 좁힘
  skirt_flared.yaml / skirt_trumpet.yaml / skirt_gored.yaml
  skirt_gather.yaml / skirt_highwaist.yaml / skirt_divided.yaml / skirt_pleated.yaml
  pants_basic.yaml          제허리 팬츠 원형 — 뒤중심 각도가 바지의 성격을 정한다
  pants_hipbone.yaml        힙본 팬츠 (허리선 3" 내림)
  pants_onetuck.yaml / pants_tapered.yaml     원턱 · 투턱(테이퍼드)
  pants_training.yaml / leggings.yaml         고무줄 허리 — 다트 없이 셔링
  pants_wide.yaml / pants_skinny.yaml         와이드 · 스키니
  waistband.yaml            오비 (직사각형)
  waistband_curved.yaml     곡선 오비 — 윗선(= 허리둘레)이 밑선보다 짧아 부채꼴로 휨
  elastic_band.yaml         고무줄 밴드 — 제도 길이와 완성 길이가 다르다
  *.svg                     실물 크기(mm) 그림
  verify_*.png              원본(검정) 위에 규칙 계산(빨강) 겹친 검증 그림
styles/
  sichuni_with_sleeve.yaml  스타일 = 원형 묶음 + 치수 연결 (몸판 암홀 길이 → 소매 앞AH·뒤AH 자동)
  shirt_collar_blouse.yaml  셔츠 칼라 블라우스 = 몸판 + 소매 + 칼라밴드(목선 길이 자동) + 칼라(밴드 윗선 자동) + 커프스 + 견보루
  basic_skirt.yaml          제허리 스커트 = 스커트 원형 + 오비 (허리 치수 자동)
  hipbone_skirt.yaml        힙본 스커트 = 힙본 원형 + 앞·뒤 곡선 오비 (밑선 길이 자동)
  aline_skirt.yaml          A라인 스커트 = A라인 원형 + 앞·뒤 곡선 오비
  tapered_skirt.yaml        테이퍼드 스커트 = 테이퍼드 원형 + 앞·뒤 곡선 오비
docs/drafting_notes.md      확인된 제도 규칙 · 미확인 사항 기록
docs/sleeve_survey.md       아이템별 소매산 조정값·이세 표와 암홀–소매 관계
tools/verify_block.py       규칙 ↔ 원본 도면 겹침 검증 · 곡선 핸들 맞춤
tools/measure.py            원형을 계산해 치수·점·선 길이를 찍어 본다 (--svg 로 다시 그림)
tools/dump_item.py          아이템 한 개의 도면 선 · 주석 · 유의사항을 한 번에 훑어본다
tests/                      python -m unittest discover -s tests
```

원형 파일 형식과 점 규칙 종류는 `patterncad/block.py` 머리말에, 설계 원칙과 로드맵은 `docs/PLAN.md` 에 있다.

```
python tools/verify_block.py blocks/sichuni_basic.yaml --page 44          # 편차 보고 + 겹침 그림
python tools/verify_block.py blocks/sichuni_basic.yaml --page 44 --fit    # 곡선 핸들을 원본에 맞춤
```

## 진행 상황

포트폴리오 **42 아이템(스커트 11 · 팬츠 8 · 상의 12 + 원형 2 · 자켓 9)을 모두 규칙으로 옮겼다.**
각 원형 파일 머리말에 해당 페이지의 유의사항을 그대로 적어 두었고,
계산된 가슴·허리·엉덩이·밑단이 사이즈표와 맞는지 테스트로 확인한다.

## 다음 단계

1. 아이템별 순차 검토 — `docs/drafting_notes.md` 의 "확인이 필요한 것" 목록부터
2. 조작 엔진(다트 이동 · 절개-벌림 · 여유 변경)을 명시적인 조작으로
3. 사이즈 체계(55/66 · S/M/L · 숫자 등 선택, 기준 사이즈 선택)와 핏 4단계 → 자동 생성
4. 조각화 · DXF 입출력 · 그레이딩 · 마카 · 데스크톱 UI

## 관련 저장소

- [cm_inch](https://github.com/keystone5741-ctrl/cm_inch) — cm ↔ inch 실시간 변환기. 인치 분수 파싱 로직을 여기서 가져다 쓸 예정.
