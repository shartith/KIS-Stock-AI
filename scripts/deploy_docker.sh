#!/bin/bash

# 설정
DOCKER_ID="shartith0106"
IMAGE_NAME="kis-stock-ai"
TAG_LATEST="latest"
TAG_VER="1.0.7"

FULL_IMAGE_LATEST="$DOCKER_ID/$IMAGE_NAME:$TAG_LATEST"
FULL_IMAGE_VER="$DOCKER_ID/$IMAGE_NAME:$TAG_VER"

echo "🚀 Docker 이미지 빌드 및 배포 시작..."
echo "Target: $FULL_IMAGE_VER (and $TAG_LATEST)"

# 프로젝트 루트로 이동 (스크립트가 어디서 실행되든 루트 기준으로 동작)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "📂 작업 디렉토리: $(pwd)"

# 1. 로그인 확인
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker가 실행 중이지 않거나 권한이 없습니다."
    exit 1
fi

# 2. 멀티 플랫폼 빌드 (가능한 경우)
# Mac(M1/M2)에서 Linux 서버(amd64)로 배포하려면 buildx 사용 권장
# if docker buildx version > /dev/null 2>&1; then
#     echo "🏗️ Buildx를 사용하여 Linux/AMD64 빌드 중... (안정성 모드)"
#     docker buildx build --platform linux/amd64 \
#       -t "$FULL_IMAGE_LATEST" \
#       -t "$FULL_IMAGE_VER" \
#       --push .
# else
    echo "⚠️ Buildx를 우회합니다. 표준 빌드를 수행합니다 (Native Architecture)."
    echo "🏗️ 이미지 빌드 중..."
    # 플랫폼 옵션 제거 (Native Build)
    docker build -t "$FULL_IMAGE_LATEST" -t "$FULL_IMAGE_VER" .
    
    echo "⬆️ Docker Hub로 푸시 중..."
    docker push "$FULL_IMAGE_LATEST"
    docker push "$FULL_IMAGE_VER"
# fi

if [ $? -eq 0 ]; then
    echo "✅ 배포 완료! ($FULL_IMAGE_VER)"
    echo "👉 서버에서 실행: docker pull $FULL_IMAGE_LATEST && docker-compose up -d"
else
    echo "❌ 배포 실패"
    exit 1
fi
