# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

RUN apk add --no-cache zip

COPY frontend/package.json ./
RUN npm install

COPY frontend/ .

# Package the browser extension from source so the distributed ZIP
# always matches the current browser-extension/ directory
COPY browser-extension/ /tmp/browser-extension/
RUN cd /tmp && zip -r /app/public/jellydj-extension.zip browser-extension/

RUN npm run build

# Serve stage
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=builder /app/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
