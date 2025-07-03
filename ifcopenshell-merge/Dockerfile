FROM python:3.10-slim
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y software-properties-common

COPY requirements.txt .
RUN pip install -r requirements.txt

WORKDIR /usr/src/app
