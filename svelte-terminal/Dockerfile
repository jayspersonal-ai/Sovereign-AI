FROM node:20-alpine

WORKDIR /app

# Install dependencies first for Docker layer caching
COPY package*.json ./
RUN npm install --legacy-peer-deps --no-audit --no-fund

# Copy configuration and source
COPY svelte.config.js vite.config.ts tsconfig*.json index.html ./
COPY src/ ./src/

# Expose sovereign frontend port 8100
EXPOSE 8100

# Start Vite dev server accessible externally on 0.0.0.0:8100
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8100"]
