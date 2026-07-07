# ---- AI Chat 웹앱 컨테이너 이미지 -------------------------------------------
# 어느 컴퓨터에서든 `docker compose up` 한 번으로 동일하게 실행되도록 패키징한다.
FROM python:3.12-slim

# 파이썬 런타임 설정: 로그 즉시 출력, .pyc 미생성
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 1) 의존성 먼저 설치 (소스보다 앞에 둬서 레이어 캐시 활용 → 재빌드 빠름)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) 앱 소스 복사 (main.py 실행에 필요한 것만; 레거시 app.py 는 제외)
COPY main.py .
COPY backend/ backend/
COPY static/ static/

# 3) 데이터/업로드 디렉터리 (실행 시 volume 으로 마운트되지만 기본 생성)
RUN mkdir -p data uploads

# 컨테이너가 8000 포트로 서비스 (main.py 가 0.0.0.0:8000 로 바인딩)
EXPOSE 8000

CMD ["python", "main.py"]
