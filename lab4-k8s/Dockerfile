FROM python:3.12-slim

# to avoid committing docker files to repository (/temp is in gitignore)
WORKDIR /tmp

COPY app.py .

ARG APP_VERSION=1.0.0
ARG APP_COLOR=blue

ENV APP_VERSION=${APP_VERSION}
ENV APP_COLOR=${APP_COLOR}
ENV PORT=8080

EXPOSE 8080

CMD ["python", "app.py"]