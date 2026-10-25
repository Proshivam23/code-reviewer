FROM python:3.12-slim

RUN pip install --no-cache-dir bandit==1.9.4 ruff==0.16.10 \
    && useradd --create-home --shell /usr/sbin/nologin analyzer

WORKDIR /work
USER analyzer
