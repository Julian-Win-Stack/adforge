# The page and the door to the app: the frontend built once, served by Caddy, which also
# passes /api on to Django, serves /media from the shared media volume and holds the lock
# (deploy/README.md). Built from the repo root (deploy/compose.yml).
FROM node:24 AS page
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
# The frontend's test helpers are type-checked during the build.
COPY backend/tests/fixtures/sent_list.json /app/backend/tests/fixtures/sent_list.json
RUN npm run build

FROM caddy:2
COPY --from=page /app/frontend/dist /srv/site
COPY deploy/Caddyfile /etc/caddy/Caddyfile
