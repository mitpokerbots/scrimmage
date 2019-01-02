FROM python:2.7-wheezy

RUN useradd -ms /bin/bash worker

# copy only the files needed for pip install
COPY requirements.txt /home/worker/requirements.txt
RUN pip install --upgrade pip
RUN pip install -r /home/worker/requirements.txt

COPY requirements-worker.txt /home/worker/requirements-worker.txt
RUN pip install -r /home/worker/requirements-worker.txt

ENV DEBIAN_FRONTEND noninteractive

RUN apt-get install -yq gcc scons libpoker-eval autoconf libtool git default-jdk

USER worker

# copy the rest of the app
COPY . /home/worker

WORKDIR /home/worker
