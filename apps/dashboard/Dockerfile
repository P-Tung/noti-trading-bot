FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

ENV NODE_ENV=production

CMD ["sh", "-c", "HOSTNAME=0.0.0.0 PORT=${PORT:-3000} node .next/standalone/server.js"]
