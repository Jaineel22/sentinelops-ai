# SentinelOps frontend (Phase 10) — Next.js standalone build.
FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/frontend/package.json apps/frontend/package-lock.json* ./
RUN npm ci || npm install

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/frontend/ ./
ARG INCIDENT_API_URL=http://localhost:8002
ARG RCA_API_URL=http://localhost:8004
ARG REMEDIATION_API_URL=http://localhost:8005
ARG DETECTOR_API_URL=http://localhost:8003
ARG AUTH_API_URL=http://localhost:8000
ENV INCIDENT_API_URL=$INCIDENT_API_URL \
    RCA_API_URL=$RCA_API_URL \
    REMEDIATION_API_URL=$REMEDIATION_API_URL \
    DETECTOR_API_URL=$DETECTOR_API_URL \
    AUTH_API_URL=$AUTH_API_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
RUN addgroup -S app && adduser -S app -G app
COPY --from=builder /app/public ./public
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static ./.next/static
USER app
EXPOSE 3000
CMD ["node", "server.js"]
