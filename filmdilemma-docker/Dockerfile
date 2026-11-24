FROM nginx:1.27-alpine

ENV API_UPSTREAM=https://api.filmdilemma.com
# Bara ${API_UPSTREAM} ska bytas ut i mallen, inte nginx egna $-variabler
ENV NGINX_ENVSUBST_FILTER=^API_UPSTREAM$

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.d-40-api-url.sh /docker-entrypoint.d/40-api-url.sh
COPY site/ /usr/share/nginx/html/

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
