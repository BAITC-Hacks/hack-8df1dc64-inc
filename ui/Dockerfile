FROM node:22-alpine
WORKDIR /app/ui
COPY ui /app/ui
ENV HOST=0.0.0.0 PORT=3000
USER node
EXPOSE 3000
CMD ["node", "server.mjs"]
