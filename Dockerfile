FROM node:20-slim AS dashboard-build

WORKDIR /build
COPY package.json package-lock.json ./
COPY dashboard ./dashboard
RUN npm ci && npm run dashboard:build

FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=dashboard-build /build/dashboard/dist ./dashboard/dist
RUN pip install --no-cache-dir . && mkdir -p /app/data

EXPOSE 8800
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV OPENALPHASTACK_HOST=0.0.0.0

CMD ["openalphastack", "app", "start"]
