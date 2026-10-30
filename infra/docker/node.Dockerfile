FROM node:22-bookworm-slim@sha256:d97982a484ac9a319ff496441d4af5cd2d2c66bf82b642a311ffda392f4859da AS dependencies
WORKDIR /app
RUN npm install --global pnpm@9.15.4
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY packages/contracts/package.json packages/contracts/package.json
COPY apps/bff/package.json apps/bff/package.json
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile
COPY packages/contracts packages/contracts
RUN pnpm --filter @yta/contracts build

FROM dependencies AS bff-build
COPY apps/bff apps/bff
RUN pnpm --filter @yta/bff build && pnpm --filter @yta/bff deploy --prod --frozen-lockfile /out

FROM node:22-bookworm-slim@sha256:d97982a484ac9a319ff496441d4af5cd2d2c66bf82b642a311ffda392f4859da AS bff
WORKDIR /app
ENV NODE_ENV=production YTA_BFF_HOST=0.0.0.0
COPY --from=bff-build --chown=node:node /out ./
USER node
EXPOSE 3100
CMD ["node", "dist/main.js"]

FROM dependencies AS web-build
ARG YTA_BFF_ORIGIN=http://yta-bff:3100
ENV YTA_BFF_ORIGIN=$YTA_BFF_ORIGIN NEXT_TELEMETRY_DISABLED=1 YTA_STANDALONE=1
COPY apps/web apps/web
RUN pnpm --filter @yta/web build

FROM node:22-bookworm-slim@sha256:d97982a484ac9a319ff496441d4af5cd2d2c66bf82b642a311ffda392f4859da AS web
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000 NEXT_TELEMETRY_DISABLED=1
COPY --from=web-build --chown=node:node /app/apps/web/.next/standalone ./
COPY --from=web-build --chown=node:node /app/apps/web/.next/static ./apps/web/.next/static
USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
