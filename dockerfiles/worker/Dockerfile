FROM python:3.7

RUN useradd -ms /bin/bash worker

# copy only the files needed for pip install
RUN pip install --upgrade pip
RUN pip install Cython

COPY requirements.txt /home/worker/requirements.txt
RUN pip install -r /home/worker/requirements.txt

COPY requirements-worker.txt /home/worker/requirements-worker.txt
RUN pip install -r /home/worker/requirements-worker.txt

ENV DEBIAN_FRONTEND noninteractive

RUN apt-get update -y
RUN apt-get upgrade -y
RUN apt-get install -yq default-jdk
RUN apt-get install -yq gcc g++ scons autoconf libtool git libboost-all-dev libc6-dev
RUN apt-get install -yq nano

USER worker

# copy the rest of the app
COPY ./scrimmage /home/worker/scrimmage
COPY ./deps/engine /home/worker/engine

WORKDIR /home/worker
