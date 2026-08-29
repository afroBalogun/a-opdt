# A-OPDT frontend (Vite + React), built once and served by nginx.
#
# VITE_API_URL is a BUILD argument, not a runtime variable: Vite inlines it at
# build time. src/api.ts falls back to http://localhost:8500, which resolves to
# the *viewer's own machine* and fails for everyone but the developer - so this
# must be set to the address the browser will use to reach the backend.
FROM node:20-slim AS build

WORKDIR /src
COPY webapp/frontend/package.json webapp/frontend/package-lock.json ./
RUN npm ci

COPY webapp/frontend/ ./
ARG VITE_API_URL=http://localhost:8500
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginx:alpine
COPY --from=build /src/dist /usr/share/nginx/html
# React Router uses client-side routes; unknown paths must fall through to
# index.html rather than 404.
RUN printf 'server {\n\
  listen 80;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  location / { try_files $uri $uri/ /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
