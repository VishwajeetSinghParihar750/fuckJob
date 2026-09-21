FROM node:22-alpine AS build
WORKDIR /dashboard
COPY package.json ./
RUN npm install
COPY . ./
RUN npm run build

FROM nginx:1.27-alpine
RUN apk add --no-cache apache2-utils
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY entrypoint.sh /usr/local/bin/dashboard-entrypoint
COPY --from=build /dashboard/dist /usr/share/nginx/html
RUN chmod +x /usr/local/bin/dashboard-entrypoint
ENTRYPOINT ["/usr/local/bin/dashboard-entrypoint"]
CMD ["nginx", "-g", "daemon off;"]
EXPOSE 80
