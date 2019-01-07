FROM python:2.7-jessie

RUN useradd -ms /bin/bash worker

# copy only the files needed for pip install
COPY requirements.txt /home/worker/requirements.txt
RUN pip install --upgrade pip
RUN pip install -r /home/worker/requirements.txt

COPY requirements-worker.txt /home/worker/requirements-worker.txt
RUN pip install -r /home/worker/requirements-worker.txt

ENV DEBIAN_FRONTEND noninteractive

RUN echo 'deb http://ftp.debian.org/debian jessie-backports main' >> /etc/apt/sources.list.d/sources.list
RUN apt-get update -y
RUN apt-get upgrade -y
RUN apt-get -t jessie-backports install -yq openjdk-8-jdk-headless
RUN apt-get install -yq gcc g++ scons autoconf libtool git libpoker-eval libpoker-eval-dev libboost-all-dev libc6-dev
RUN apt-get install -yq nano

COPY ./deps/pbots_calc/libpbots_calc.so /usr/lib/libpbots_calc.so
COPY ./deps/pbots_calc/pbots_calc.h /usr/include/pbots_calc.h
COPY ./deps/pbots_calc/pbots_calc.py /usr/local/lib/python2.7/site-packages/pbots_calc.py

USER worker

# copy the rest of the app
COPY . /home/worker

WORKDIR /home/worker
