FROM node:24-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY index.html ./
COPY src ./src
ARG VITE_API_BASE=
ARG VITE_AIG_DEMO_MODE=false
ENV VITE_API_BASE=$VITE_API_BASE
ENV VITE_AIG_DEMO_MODE=$VITE_AIG_DEMO_MODE
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
