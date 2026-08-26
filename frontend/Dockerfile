# SystemDecoded frontend image.
# Development-oriented: Compose mounts ./frontend over /app and runs `npm run dev`.
# node_modules lives in a named volume so the host (Windows) and container
# (Linux) never share native builds.

FROM node:24-alpine

WORKDIR /app

# Install dependencies first so source edits do not reinstall the tree.
COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
