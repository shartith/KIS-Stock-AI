# KIS-Stock-AI Docker 배포 가이드

## 다른 컴퓨터에 설치

`deploy/` 폴더만 복사하면 됩니다 (소스코드 불필요).

```bash
# 1. 이미지 로드
docker load < kis-stock-ai.tar.gz

# 2. 환경 설정
cp .env.example .env

# 3. 실행
docker compose up -d
```

브라우저에서 `http://localhost:8080` 접속 → Settings에서 API 키 설정

## Local AI 연동

Settings 페이지에서 **로컬 LLM URL** 입력:

| AI 서버 | URL 예시 |
|---------|---------|
| BitNet.cpp | `http://host.docker.internal:8002` |
| Ollama | `http://host.docker.internal:11434` |
| Transformers | `http://host.docker.internal:5000` |
| vLLM | `http://host.docker.internal:8000` |

> ⚠️ Docker 내부에서 호스트의 AI 서버에 접근하려면 `localhost` 대신 `host.docker.internal`을 사용하세요.

## 명령어

```bash
docker compose ps          # 상태
docker compose logs -f     # 로그
docker compose down        # 중지
docker compose restart     # 재시작
```

## 데이터 백업

```bash
docker run --rm -v kis-data:/data -v $(pwd):/backup alpine tar czf /backup/data-backup.tar.gz /data
```
