FROM python:2.7-wheezy

RUN useradd -ms /bin/bash worker

# copy only the files needed for pip install
COPY requirements.txt /home/worker/requirements.txt
RUN pip install --upgrade pip
RUN pip install -r /home/worker/requirements.txt

COPY requirements-worker.txt /home/worker/requirements-worker.txt
RUN pip install -r /home/worker/requirements-worker.txt

ENV DEBIAN_FRONTEND noninteractive

RUN apt-get update
RUN apt-get upgrade
RUN apt-get install -yq gcc scons libpoker-eval autoconf libtool git openjdk-8-jdk

COPY ./deps/pbots_calc/libpbots_calc.so /usr/lib/libpbots_calc.so
COPY ./deps/pbots_calc/pbots_calc.h /usr/include/pbots_calc.h
COPY ./deps/pbots_calc/pbots_calc.py /usr/local/lib/python2.7/site-packages/pbots_calc.py

USER worker

# copy the rest of the app
COPY . /home/worker

WORKDIR /home/worker
