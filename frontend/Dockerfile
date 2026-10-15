# WafGuard AI — UI image (Phase 9). Build from the REPO ROOT:
#   docker build -f frontend/Dockerfile -t wafguard-ui .
# Vite bakes VITE_API_BASE_URL in at build time; "/api" makes the browser call
# the same origin, and nginx (deploy/nginx.conf) proxies /api to the API service.
FROM node:20-alpine AS build
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /ui/dist /usr/share/nginx/html
EXPOSE 80
