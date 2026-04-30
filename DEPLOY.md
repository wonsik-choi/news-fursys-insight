# Fursys-Insight 배포 가이드 (Vercel)

GitHub Actions(빌드) + Vercel(호스팅) 조합으로 배포합니다.

## 0. 시작 전 준비물
- GitHub 계정
- Vercel 계정 (GitHub로 가입하면 연동 한 번에 됨)
- Anthropic API 키 (재발급 권장)
- (선택) Slack Incoming Webhook URL

---

## 1단계 — GitHub 레포지토리 만들기

1. GitHub에서 **새 레포지토리 생성**: 예) `fursys-insight`
   - **Private** 권장 (자사 정보가 들어가니까)
2. 로컬에 다음 파일들을 넣고 git push:
   ```
   fursys-insight/
   ├── newsletter.py
   ├── run_chunked.py
   ├── requirements.txt
   ├── vercel.json
   ├── .gitignore
   └── .github/
       └── workflows/
           └── daily.yml
   ```
3. 명령어:
   ```bash
   git init
   git add .
   git commit -m "init"
   git remote add origin https://github.com/{본인}/fursys-insight.git
   git branch -M main
   git push -u origin main
   ```

---

## 2단계 — GitHub Secrets 등록

레포 페이지에서 **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| 이름 | 값 |
|------|---|
| `ANTHROPIC_API_KEY` | sk-ant-... (새로 발급한 키) |
| `SLACK_WEBHOOK_URL` | (선택) Slack Webhook URL |
| `FURSYS_INSIGHT_URL` | (선택) Vercel 배포 URL — 4단계 후 채움 |

---

## 3단계 — 첫 빌드 트리거

1. 레포 페이지에서 **Actions** 탭
2. 좌측 **"Daily Fursys-Insight Build"** 워크플로우 클릭
3. 우측 **"Run workflow"** 버튼 → **Run workflow**
4. 2~5분 후 완료. `public/index.html` + `public/archive/...json` + `archive/...json` 자동 commit & push 됨

---

## 4단계 — Vercel 연동

1. https://vercel.com/new 접속
2. **GitHub 레포** (`fursys-insight`) 선택 → **Import**
3. 설정:
   - **Framework Preset**: Other
   - **Root Directory**: `./`
   - **Build Command**: 비워두기 (vercel.json이 처리)
   - **Output Directory**: `public`
4. **Deploy** 클릭
5. 완료되면 URL 확인 (`https://fursys-insight.vercel.app` 같은 형태)
6. (옵션) GitHub Secrets에 `FURSYS_INSIGHT_URL`로 이 URL 추가 → Slack 메시지에 "전체 보기" 링크 박힘

---

## 5단계 — 자동 운영

- 매일 **KST 08:00**에 GitHub Actions가 자동 실행
- 새 `index.html` & `archive/오늘.json` 생성 → main 브랜치에 commit
- Vercel이 push 감지 → 자동 재배포 (10~30초)
- Slack Webhook 등록되어 있으면 같이 발송

직원들은 그냥 Vercel URL 즐겨찾기 → 매일 아침 새 인사이트 확인.

---

## 비용

| 항목 | 무료 한도 | 우리 사용량 | 결과 |
|------|-----------|-------------|------|
| GitHub Actions | 월 2,000분 | 월 ~150분 (5분 × 30일) | 무료 |
| Vercel | 트래픽 100GB/월 | 정적 페이지라 거의 안 씀 | 무료 |
| Anthropic Haiku | 종량제 | 월 ~7,000원 | 유일한 비용 |

총 한 달 1만 원 미만으로 24/7 운영.

---

## 트러블슈팅

**Actions 실행 실패** — 레포 Settings → Actions → General → "Workflow permissions"를 **Read and write permissions**로 설정 (commit 푸시 권한 필요)

**Vercel 빌드 에러** — `vercel.json`의 `outputDirectory: "public"`이 맞는지, Action이 `public/` 폴더를 만들었는지 확인

**Action은 성공했는데 사이트가 비어있음** — 첫 실행 후 5단계 완료 전이면 정상. 두 번째 commit 후부터 보임

**시간을 KST 08:00이 아닌 다른 시간으로 바꾸고 싶다** — `.github/workflows/daily.yml`의 `cron: '0 23 * * *'` 부분 수정. UTC 기준 (KST = UTC+9). 예: KST 9시 → UTC 0시 → `'0 0 * * *'`

**수동으로 한 번 돌리고 싶다** — Actions 탭 → 워크플로우 → "Run workflow" 버튼

---

## 권한 / 접근 제어

기본은 Vercel URL 아는 사람 누구나 접근 가능 (사실상 공개).

회사 내부 직원만 보게 제한하려면:
1. **Vercel Password Protection** (Pro 플랜 $20/월): 사이트 전체에 비밀번호
2. **Vercel SSO Protection** (Enterprise): SSO 연동
3. **사내 망 호스팅으로 이전**: 정말 민감한 단계 가면 사내 서버로 옮기기

당장은 자사 정보가 외부 뉴스에 대한 코멘트 수준이라 큰 문제 없을 수 있지만, 자사 브랜드 소식 카테고리에 내부 정보가 들어갈 가능성도 있으니 처음부터 Pro 플랜 비밀번호 보호를 권장합니다.
