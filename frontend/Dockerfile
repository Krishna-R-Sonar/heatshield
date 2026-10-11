FROM node:24-alpine

WORKDIR /app/frontend

COPY --chown=node:node frontend/package.json frontend/package-lock.json ./
RUN chown node:node /app/frontend
USER node
RUN npm ci

COPY --chown=node:node frontend/ ./

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
