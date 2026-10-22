# The page and the door to the app: the frontend built once, served by Caddy, which also
# passes /api on to Django, serves /media from the shared media volume and holds the lock
# (deploy/README.md). Built from the repo root (deploy/compose.yml).
FROM node:24 AS page
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM caddy:2
COPY --from=page /app/dist /srv/site
COPY deploy/Caddyfile /etc/caddy/Caddyfile
