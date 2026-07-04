# syntax=docker/dockerfile:1
#
# Frontend image: `pnpm build` output served by nginx, with the backend's
# routes (spec section 23) proxied through — mirrors the paths the Vite dev
# server proxies in vite.config.ts.
#
# Build context is ./frontend (see docker-compose.yml at repo root).
FROM node:20-slim AS build

RUN corepack enable && corepack prepare pnpm@10 --activate

WORKDIR /app

# Install deps first so source edits don't bust this layer.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
