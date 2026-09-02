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

## 다음 단계

1. `extracted/` 의 치수 주석을 **점·선 구성 규칙**(좌표가 아니라 "A에서 아래로 B/4 + 여유")으로 옮겨 원형 정의 파일을 만든다. 시추니 기본 원형부터.
2. 그 규칙으로 원형을 다시 그려 원본 도면과 겹쳐 검증한다.
3. `core/`(기하 · 치수 · 원형 · 그레이딩 · 마카, 화면 없음) → `io/`(DXF/HPGL/SVG) → `ui/`(데스크톱) 순으로 올린다.

## 관련 저장소

- [cm_inch](https://github.com/keystone5741-ctrl/cm_inch) — cm ↔ inch 실시간 변환기. 인치 분수 파싱 로직을 여기서 가져다 쓸 예정.
