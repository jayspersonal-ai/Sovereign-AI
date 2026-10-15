FROM node:20-alpine

WORKDIR /app

# Install dependencies first for Docker layer caching
COPY package*.json ./
RUN npm install --legacy-peer-deps --no-audit --no-fund

# Copy configuration and source.
# postcss.config.js and tailwind.config.js must be here, or Vite never runs
# Tailwind and the page comes out unstyled.
COPY svelte.config.js vite.config.ts tsconfig*.json postcss.config.js tailwind.config.js index.html ./
COPY src/ ./src/

# Lyra Studio (storage viewer) runs on port 8003
EXPOSE 8003

# Start Vite dev server accessible externally on 0.0.0.0:8003
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8003"]
