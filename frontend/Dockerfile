FROM node:24-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY index.html ./
COPY src ./src
ARG VITE_API_BASE=http://localhost:8000
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build
FROM node:24-slim
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
CMD ["serve","-s","dist","-l","5173"]
